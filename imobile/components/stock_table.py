"""Stock table component."""
import reflex as rx
from imobile.states.portfolio_state import PortfolioState, Stock


def stock_table() -> rx.Component:
    """Display stock holdings in a table format."""
    
    def stock_row(stock: Stock) -> rx.Component:
        """Create a table row for a stock."""
        return rx.table.row(
            # Name/Code
            rx.table.cell(
                rx.vstack(
                    rx.text(stock.name, weight="medium", size="2"),
                    rx.text(stock.code, size="1", color="gray"),
                    spacing="1",
                    align="start",
                ),
            ),
            # Current Price
            rx.table.cell(
                rx.text(
                    stock.price,
                    weight="medium",
                    size="2",
                    style={"font_variant_numeric": "tabular-nums"},
                ),
                text_align="right",
            ),
            # Change
            rx.table.cell(
                rx.text(
                    rx.cond(
                        stock.change >= 0,
                        f"+{stock.change}(+{stock.change_percent}%)",
                        f"{stock.change}({stock.change_percent}%)",
                    ),
                    weight="medium",
                    size="2",
                    color=rx.cond(stock.change >= 0, "#e74c3c", "#27ae60"),
                    style={"font_variant_numeric": "tabular-nums"},
                ),
                text_align="right",
            ),
            # Market Value
            rx.table.cell(
                rx.text(
                    stock.volume,
                    size="2",
                    style={"font_variant_numeric": "tabular-nums"},
                ),
                text_align="right",
            ),
            # Holdings
            rx.table.cell(
                rx.text(
                    stock.amount,
                    size="2",
                    style={"font_variant_numeric": "tabular-nums"},
                ),
                text_align="right",
            ),
            # Cost
            rx.table.cell(
                rx.text(
                    stock.market_value,
                    size="2",
                    style={"font_variant_numeric": "tabular-nums"},
                ),
                text_align="right",
            ),
            # Float Change
            rx.table.cell(
                rx.text(
                    rx.cond(
                        stock.float_change >= 0,
                        f"+{stock.float_change}(+{stock.float_change_percent}%)",
                        f"{stock.float_change}({stock.float_change_percent}%)",
                    ),
                    weight="medium",
                    size="2",
                    color=rx.cond(stock.float_change >= 0, "#e74c3c", "#27ae60"),
                    style={"font_variant_numeric": "tabular-nums"},
                ),
                text_align="right",
            ),
            # Cumulative Change
            rx.table.cell(
                rx.text(
                    rx.cond(
                        stock.cumulative_change >= 0,
                        f"+{stock.cumulative_change}(+{stock.cumulative_change_percent}%)",
                        f"{stock.cumulative_change}({stock.cumulative_change_percent}%)",
                    ),
                    weight="medium",
                    size="2",
                    color=rx.cond(stock.cumulative_change >= 0, "#e74c3c", "#27ae60"),
                    style={"font_variant_numeric": "tabular-nums"},
                ),
                text_align="right",
            ),
            # Actions
            rx.table.cell(
                rx.hstack(
                    rx.button("记录", variant="ghost", size="1", color_scheme="blue"),
                    rx.button("卖出", variant="ghost", size="1", color_scheme="blue"),
                    rx.button(
                        rx.icon("x", size=14),
                        variant="ghost",
                        size="1",
                        on_click=PortfolioState.remove_stock(stock.code),
                    ),
                    spacing="2",
                    justify="end",
                ),
                text_align="right",
            ),
        )
    
    return rx.box(
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    rx.table.column_header_cell("名称/代码", align="left"),
                    rx.table.column_header_cell("现价", align="right"),
                    rx.table.column_header_cell("涨跌", align="right"),
                    rx.table.column_header_cell("市值", align="right"),
                    rx.table.column_header_cell("持仓", align="right"),
                    rx.table.column_header_cell("成本/成本", align="right"),
                    rx.table.column_header_cell("浮动盘变化", align="right"),
                    rx.table.column_header_cell("累计变化", align="right"),
                    rx.table.column_header_cell("操作", align="right"),
                ),
            ),
            rx.table.body(
                rx.foreach(
                    PortfolioState.stocks,
                    stock_row,
                ),
            ),
            variant="surface",
            width="100%",
        ),
        border_radius="8px",
        border="1px solid var(--gray-5)",
        overflow="hidden",
        width="100%",
    )
