import reflex as rx
import adata
import tushare as ts
import pandas as pd
import os
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import ta
from typing import List, Dict, Any
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

# Initialize TuShare
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = None
if TUSHARE_TOKEN:
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()

class Sector(BaseModel):
    ts_code: str
    name: str
    pct_chg: float
    close: float
    trade_date: str
    source: str = "THS"

class DailySectorData(BaseModel):
    date: str
    sectors: List[Sector]

class Stock(BaseModel):
    ts_code: str
    name: str
    pct_chg: float
    close: float

class SectorHistoryState(rx.State):
    start_date: str = datetime.now().strftime("%Y-%m-%d")
    end_date: str = datetime.now().strftime("%Y-%m-%d")
    daily_hot_sectors: List[DailySectorData] = []
    
    selected_sector: Sector = Sector(ts_code="", name="", pct_chg=0.0, close=0.0, trade_date="")
    sector_chart_figure: go.Figure = go.Figure()
    has_chart_data: bool = False
    sector_top_stocks: List[Stock] = []
    related_ths_sectors: List[Sector] = []
    _ths_concepts_cache: List[Dict] = []
    
    is_loading: bool = False
    error_message: str = ""

    @rx.event
    def set_start_date(self, date: str):
        self.start_date = date

    @rx.event
    def set_end_date(self, date: str):
        self.end_date = date

    @rx.event
    def load_data(self):
        if not pro:
            self.error_message = "TuShare token not found. Please set TUSHARE_TOKEN environment variable."
            return

        self.is_loading = True
        yield
        self.error_message = ""
        self.daily_hot_sectors = []
        
        try:
            # Convert dates to YYYYMMDD
            s_date = self.start_date.replace("-", "")
            e_date = self.end_date.replace("-", "")
            
            # Get all sectors name
            concepts = pro.ths_index(exchange='A', type='N')
            code_to_name = dict(zip(concepts['ts_code'], concepts['name']))

            # Get trading calendar
            cal = pro.trade_cal(exchange='', start_date=s_date, end_date=e_date, is_open='1')
            trading_days = cal['cal_date'].tolist()
            
            new_daily_sectors = []
            for trade_date in trading_days:
                df = pro.ths_daily(trade_date=trade_date)
                if df.empty:
                    continue
                
                # Remove ts_code not in code_to_name.
                df = df[df['ts_code'].isin(code_to_name.keys())]

                # Sort by pct_chg
                df = df.sort_values('pct_change', ascending=False).head(10)
                
                sectors = []
                for _, row in df.iterrows():
                    name_str = code_to_name.get(row['ts_code'])
                    # Strip .TI for display
                    display_code = row['ts_code'].replace(".TI", "")
                    new_name = f"{name_str}({display_code})"
                    sectors.append(Sector(
                        ts_code=row['ts_code'],
                        name=new_name,
                        pct_chg=row['pct_change'],
                        close=row['close'],
                        trade_date=trade_date
                    ))
                
                new_daily_sectors.append(DailySectorData(
                    date=trade_date,
                    sectors=sectors
                ))
            
            self.daily_hot_sectors = new_daily_sectors
            self._enrich_sector_names()
            
            # Add DC Current Data
            dc_sectors = self._load_dc_data()
            if dc_sectors:
                self.daily_hot_sectors.insert(0, DailySectorData(
                    date=f"Current DC ({datetime.now().strftime('%H:%M')})",
                    sectors=dc_sectors
                ))

        except Exception as e:
            self.error_message = f"Error loading data: {str(e)}"
        finally:
            self.is_loading = False
            yield

    def _enrich_sector_names(self):
        if not pro:
            return
        try:
            concepts = pro.ths_index(exchange='A', type='N')
            code_to_name = dict(zip(concepts['ts_code'], concepts['name']))
            
            # Cache for cross-verification
            self._ths_concepts_cache = concepts[['ts_code', 'name']].to_dict('records')
            
            updated_daily_sectors = []
            for day_data in self.daily_hot_sectors:
                new_sectors = []
                for sector in day_data.sectors:
                    # Create new Sector with updated name
                    raw_name = code_to_name.get(sector.ts_code)
                    display_code = sector.ts_code.replace(".TI", "")
                    
                    if raw_name:
                        new_name = f"{raw_name}({display_code})"
                    else:
                        new_name = sector.name
                        if f"({display_code})" not in new_name:
                             new_name = f"{new_name}({display_code})"
                    
                    new_sector = Sector(
                        ts_code=sector.ts_code,
                        name=new_name,
                        pct_chg=sector.pct_chg,
                        close=sector.close,
                        trade_date=sector.trade_date
                    )
                    new_sectors.append(new_sector)
                
                updated_daily_sectors.append(DailySectorData(
                    date=day_data.date,
                    sectors=new_sectors
                ))
            self.daily_hot_sectors = updated_daily_sectors
            
        except Exception as e:
            print(f"Error enriching names: {e}")

    def _load_dc_data(self) -> List[Sector]:
        try:
            # Get names
            concepts = adata.stock.info.all_concept_code_east()
            code_to_name = dict(zip(concepts['index_code'], concepts['name']))
            
            # Get market data
            df = adata.stock.market.get_market_concept_current_east()
            if df.empty:
                return []
                
            # Sort by change_pct desc
            df = df.sort_values('change_pct', ascending=False).head(20)
            
            sectors = []
            for _, row in df.iterrows():
                name_str = code_to_name.get(row['index_code'], row['index_code'])
                name = f"{name_str}({row['index_code']})"
                t_date = row['trade_date']
                if pd.isna(t_date):
                    t_date = datetime.now().strftime("%Y-%m-%d")
                else:
                    t_date = str(t_date)
                    
                sectors.append(Sector(
                    ts_code=row['index_code'],
                    name=name,
                    pct_chg=row['change_pct'],
                    close=row['price'],
                    trade_date=t_date,
                    source="DC"
                ))
            return sectors
        except Exception as e:
            print(f"Error loading DC data: {e}")
            return []

    @rx.event
    def select_sector(self, sector: Sector):
        self.selected_sector = sector
        self.load_sector_details(sector.ts_code, sector.trade_date, sector.name)
        
        # Cross-verification: Find related THS sectors if DC is selected
        if sector.source == "DC":
            self.find_related_ths_sectors(sector.name)
        else:
            self.related_ths_sectors = []

    def find_related_ths_sectors(self, dc_name: str):
        if not self._ths_concepts_cache:
            # Try to load if empty (fallback)
            self._enrich_sector_names()
            
        matches = []
        # Simple name matching
        # Remove common suffixes/prefixes for better matching if needed
        clean_dc_name = dc_name.replace("概念", "").replace("板块", "")
        
        for item in self._ths_concepts_cache:
            ths_name = item['name']
            ths_code = item['ts_code']
            
            # Check for containment
            if clean_dc_name in ths_name or ths_name in clean_dc_name:
                matches.append(Sector(
                    ts_code=ths_code,
                    name=ths_name,
                    pct_chg=0.0, # We don't have real-time data for all THS concepts here
                    close=0.0,
                    trade_date="",
                    source="THS"
                ))
        
        self.related_ths_sectors = matches[:5] # Limit to top 5 matches

    def load_sector_details(self, sector_code, trade_date, sector_name):
        if not pro:
            return

        try:
            # 1. Generate Chart using AData
            if not sector_code:
                print("Error: sector_code is empty")
                return

            # Clean code for AData (remove suffix if present)
            # Ensure sector_code is string
            sector_code = str(sector_code)
            clean_code = sector_code.split('.')[0] if '.' in sector_code else sector_code
            
            df = pd.DataFrame()
            if self.selected_sector.source == 'DC' or sector_code.startswith('BK'):
                df = adata.stock.market.get_market_concept_east(index_code=clean_code, k_type=1)
            else:
                # THS
                df = adata.stock.market.get_market_concept_ths(index_code=clean_code, k_type=1)
                
            if not df.empty:
                # AData returns 'trade_date' as string or datetime? Usually string YYYY-MM-DD or YYYYMMDD
                # Check columns: ['index_code', 'trade_time', 'trade_date', 'open', 'high', 'low', 'close', ...]
                # Filter last 60 days
                df['trade_date'] = pd.to_datetime(df['trade_date'])
                df = df.sort_values('trade_date')
                
                # Calculate MACD on full data (or larger window) to ensure valid values for the display range
                # MACD requires a warm-up period (26 days +), so calculating on just 60 days leaves the start empty.
                macd = ta.trend.MACD(close=df['close'])
                df['macd'] = macd.macd()
                df['macd_signal'] = macd.macd_signal()
                df['macd_diff'] = macd.macd_diff()
                
                # Slice for display (Last 60 records)
                df = df.tail(60)
                
                # Fetch SSE Index Data
                sse_df = pd.DataFrame()
                try:
                    start_d = df['trade_date'].min().strftime('%Y%m%d')
                    end_d = df['trade_date'].max().strftime('%Y%m%d')
                    sse_df = pro.index_daily(ts_code='000001.SH', start_date=start_d, end_date=end_d)
                    if not sse_df.empty:
                        sse_df['trade_date'] = pd.to_datetime(sse_df['trade_date'])
                        sse_df = sse_df.sort_values('trade_date')
                except Exception as e:
                    print(f"Error fetching SSE data: {e}")

                # Create Subplots
                fig = make_subplots(
                    rows=2, cols=1, 
                    shared_xaxes=True, 
                    vertical_spacing=0.05,
                    row_heights=[0.7, 0.3],
                    specs=[[{"secondary_y": True}], [{"secondary_y": False}]]
                )
                
                # 1. Candlestick (Sector)
                fig.add_trace(go.Candlestick(
                    x=df['trade_date'],
                    open=df['open'],
                    high=df['high'],
                    low=df['low'],
                    close=df['close'],
                    increasing_line_color='red', 
                    decreasing_line_color='green',
                    name='Sector'
                ), row=1, col=1, secondary_y=False)
                
                # 2. SSE Index Line (Yellow)
                if not sse_df.empty:
                    fig.add_trace(go.Scatter(
                        x=sse_df['trade_date'],
                        y=sse_df['close'],
                        mode='lines',
                        line=dict(color='yellow', width=1),
                        name='SSE Index'
                    ), row=1, col=1, secondary_y=True)
                
                # 3. MACD
                # Histogram
                fig.add_trace(go.Bar(
                    x=df['trade_date'],
                    y=df['macd_diff'],
                    marker_color=df['macd_diff'].apply(lambda x: 'red' if x >= 0 else 'green'),
                    name='MACD Hist'
                ), row=2, col=1)
                
                # MACD Line
                fig.add_trace(go.Scatter(
                    x=df['trade_date'],
                    y=df['macd'],
                    mode='lines',
                    line=dict(color='white', width=1),
                    name='DIF'
                ), row=2, col=1)
                
                # Signal Line
                fig.add_trace(go.Scatter(
                    x=df['trade_date'],
                    y=df['macd_signal'],
                    mode='lines',
                    line=dict(color='orange', width=1),
                    name='DEA'
                ), row=2, col=1)
                
                fig.update_layout(
                    title=f'{sector_name} Daily Chart ({self.selected_sector.source})',
                    xaxis_rangeslider_visible=False,
                    height=500,
                    margin=dict(l=20, r=20, t=40, b=20),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                )
                
                # Update y-axes
                fig.update_yaxes(title_text="Price", row=1, col=1, secondary_y=False)
                fig.update_yaxes(title_text="SSE", row=1, col=1, secondary_y=True, showgrid=False)
                fig.update_yaxes(title_text="MACD", row=2, col=1)
                
                self.sector_chart_figure = fig
                self.has_chart_data = True
            else:
                self.sector_chart_figure = go.Figure()
                self.has_chart_data = False

            # 2. Get Top 10 Stocks
            try:
                members = pro.ths_member(ts_code=sector_code)
            except Exception as e:
                if '没有接口访问权限' not in str(e):
                    print(f"Error getting members: {e}")
                    self.sector_top_stocks = []
                    return
                # Use clean_code for adata
                members = adata.stock.info.concept_constituent_ths(index_code=clean_code)
            if members.empty:
                self.sector_top_stocks = []
                return

            # Check column names (Tushare uses 'code', AData uses 'stock_code')
            code_col = 'code' if 'code' in members.columns else 'stock_code'
            if code_col not in members.columns:
                print(f"Error: Column {code_col} not found in members. Columns: {members.columns}")
                self.sector_top_stocks = []
                return
                
            member_codes = members[code_col].tolist()
            stock_basic = pro.stock_basic(fields='ts_code,symbol,name')
            
            valid_members = stock_basic[stock_basic['symbol'].isin(member_codes)]
            valid_ts_codes = valid_members['ts_code'].tolist()
            
            daily_stocks = pro.daily(trade_date=trade_date)
            sector_stocks_daily = daily_stocks[daily_stocks['ts_code'].isin(valid_ts_codes)]
            
            top_stocks = sector_stocks_daily.sort_values('pct_chg', ascending=False).head(10)
            top_stocks = pd.merge(top_stocks, stock_basic[['ts_code', 'name']], on='ts_code')
            
            top_stocks_list = []
            for _, row in top_stocks.iterrows():
                top_stocks_list.append(Stock(
                    ts_code=row['ts_code'],
                    name=row['name'],
                    pct_chg=row['pct_chg'],
                    close=row['close']
                ))
            self.sector_top_stocks = top_stocks_list
            
        except Exception as e:
            print(f"Error loading details: {e}")
            self.error_message = f"Error loading details: {str(e)}"


def sector_history() -> rx.Component:
    return rx.container(
        rx.vstack(
            rx.heading("Sector History Analysis", size="8"),
            rx.cond(
                SectorHistoryState.error_message != "",
                rx.callout(
                    SectorHistoryState.error_message,
                    icon="triangle_alert",
                    color_scheme="red",
                    role="alert",
                ),
            ),
            rx.hstack(
                rx.text("Start Date:"),
                rx.input(
                    type="date",
                    value=SectorHistoryState.start_date,
                    on_change=SectorHistoryState.set_start_date,
                ),
                rx.text("End Date:"),
                rx.input(
                    type="date",
                    value=SectorHistoryState.end_date,
                    on_change=SectorHistoryState.set_end_date,
                ),
                rx.button("Analyze", on_click=SectorHistoryState.load_data, loading=SectorHistoryState.is_loading),
                align="center",
                spacing="4",
            ),
            rx.cond(
                SectorHistoryState.is_loading,
                rx.progress(is_indeterminate=True, width="100%"),
            ),
            rx.divider(),
            rx.hstack(
                # Left panel: List of days and sectors
                rx.vstack(
                    rx.foreach(
                        SectorHistoryState.daily_hot_sectors,
                        lambda day_data: rx.vstack(
                            rx.heading(day_data.date, size="4"),
                            rx.foreach(
                                day_data.sectors,
                                lambda sector: rx.card(
                                    rx.hstack(
                                        rx.text(sector.name, weight="bold"),
                                        rx.text(f"{sector.pct_chg}%", color=rx.cond(sector.pct_chg >= 0, "red", "green")),
                                        justify="between",
                                    ),
                                    on_click=SectorHistoryState.select_sector(sector),
                                    cursor="pointer",
                                    width="100%",
                                    _hover={"background": "var(--gray-3)"},
                                ),
                            ),
                            width="100%",
                            spacing="2",
                            padding_bottom="4",
                        ),
                    ),
                    width="30%",
                    height="80vh",
                    overflow="auto",
                    padding_right="4",
                    border_right="1px solid var(--gray-5)",
                ),
                # Right panel: Details
                rx.vstack(
                    rx.cond(
                        SectorHistoryState.selected_sector,
                        rx.vstack(
                            #rx.heading(SectorHistoryState.selected_sector.name, size="6"),
                            #rx.text(f"Date: {SectorHistoryState.selected_sector.trade_date}"),
                            
                            # Cross-Verification UI
                            rx.cond(
                                SectorHistoryState.related_ths_sectors,
                                rx.vstack(
                                    rx.text("Verify in THS (Related Concepts):", weight="bold", color="orange"),
                                    rx.flex(
                                        rx.foreach(
                                            SectorHistoryState.related_ths_sectors,
                                            lambda s: rx.badge(
                                                s.name, 
                                                variant="outline", 
                                                cursor="pointer",
                                                on_click=SectorHistoryState.select_sector(s)
                                            ),
                                        ),
                                        wrap="wrap",
                                        spacing="2",
                                    ),
                                    padding_y="2",
                                ),
                            ),
                            
                            # Chart
                            rx.cond(
                                SectorHistoryState.has_chart_data,
                                rx.plotly(data=SectorHistoryState.sector_chart_figure, height="400px"),
                            ),
                            rx.heading("Top 10 Stocks", size="5"),
                            rx.table.root(
                                rx.table.header(
                                    rx.table.row(
                                        rx.table.column_header_cell("Code"),
                                        rx.table.column_header_cell("Name"),
                                        rx.table.column_header_cell("Change %"),
                                        rx.table.column_header_cell("Close"),
                                    ),
                                ),
                                rx.table.body(
                                    rx.foreach(
                                        SectorHistoryState.sector_top_stocks,
                                        lambda stock: rx.table.row(
                                            rx.table.cell(stock.ts_code),
                                            rx.table.cell(stock.name),
                                            rx.table.cell(
                                                stock.pct_chg,
                                                color=rx.cond(stock.pct_chg >= 0, "red", "green")
                                            ),
                                            rx.table.cell(stock.close),
                                        ),
                                    ),
                                ),
                                width="100%",
                            ),
                            width="100%",
                            spacing="4",
                        ),
                        rx.center(rx.text("Select a sector to view details"), width="100%", height="100%"),
                    ),
                    width="70%",
                    padding_left="4",
                ),
                width="100%",
                align="start",
            ),
            width="100%",
            spacing="4",
            padding="4",
        ),
    )
