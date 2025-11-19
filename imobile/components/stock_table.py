"""Stock table component."""
import reflex as rx
from imobile.states.portfolio_state import PortfolioState, Stock


def stock_table() -> rx.Component:
    """Display stock holdings in a table format."""
    
    def sortable_header(label: str, field: str) -> rx.Component:
        """Create a sortable table header with sort indicator."""
        return rx.table.column_header_cell(
            rx.hstack(
                rx.text(label, size="2"),
                rx.cond(
                    PortfolioState.sort_by == field,
                    rx.cond(
                        PortfolioState.sort_order == "desc",
                        rx.icon("arrow-down", size=14),
                        rx.icon("arrow-up", size=14),
                    ),
                    rx.box(),  # Empty box when not sorted by this field
                ),
                spacing="1",
                align="center",
                justify="end",
            ),
            align="right",
            on_click=PortfolioState.sort_stocks(field),
            style={
                "cursor": "pointer",
                "_hover": {"background": "var(--gray-3)"},
            },
        )
    
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
            # Market Value
            rx.table.cell(
                rx.text(
                    stock.market_value,
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
            # holdings/available_shares
            rx.table.cell(
                rx.hstack(
                    rx.text(
                        stock.holdings,
                        size="2",
                        style={"font_variant_numeric": "tabular-nums"},
                    ),
                    rx.text(
                        f"({stock.available_shares})",
                        size="2",
                        color="gray",
                        style={"font_variant_numeric": "tabular-nums"},
                    ),
                    spacing="0",
                    justify="end",
                ),
                text_align="right",
            ),
            # current_price/cost_basis_total
            rx.table.cell(
                rx.hstack(
                    rx.text(
                        stock.current_price,
                        size="2",
                        style={"font_variant_numeric": "tabular-nums"},
                    ),
                    rx.text(
                        f"({stock.cost_basis_total})",
                        size="2",
                        color="gray",
                        style={"font_variant_numeric": "tabular-nums"},
                    ),
                    spacing="0",
                    justify="end",
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
            #rx.table.cell(
            #    rx.text(
            #        rx.cond(
            #            stock.cumulative_change >= 0,
            #            f"+{stock.cumulative_change}(+{stock.cumulative_change_percent}%)",
            #            f"{stock.cumulative_change}({stock.cumulative_change_percent}%)",
            #        ),
            #        weight="medium",
            #        size="2",
            #        color=rx.cond(stock.cumulative_change >= 0, "#e74c3c", "#27ae60"),
            #        style={"font_variant_numeric": "tabular-nums"},
            #    ),
            #    text_align="right",
            #),
            # Actions
            rx.table.cell(
                rx.hstack(
                    rx.button(
                        "分析",
                        variant="ghost",
                        size="1",
                        color_scheme="blue",
                        on_click=PortfolioState.open_analysis_report(stock.code),
                    ),
                    rx.button(
                        "指令",
                        variant="ghost",
                        size="1",
                        color_scheme="blue",
                        on_click=PortfolioState.open_operation_cmd(stock.code),
                    ),
                    rx.button("交易", variant="ghost", size="1", color_scheme="blue"),
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
        rx.box(
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("名称/代码", align="left"),
                        sortable_header("市值", "market_value"),
                        sortable_header("涨跌/涨幅", "change_percent"),
                        sortable_header("持仓/可用", "holdings"),
                        sortable_header("现价/成本", "current_price"),
                        sortable_header("浮动盘变化", "float_change_percent"),
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
            overflow_x="auto",
            overflow_y="visible",
            width="100%",
            style={
                "-webkit-overflow-scrolling": "touch",
            },
        ),
        border_radius="8px",
        border="1px solid var(--gray-5)",
        width="100%",
    )
