"""Market statistics component."""
import reflex as rx
from imobile.states.portfolio_state import PortfolioState


def market_stats() -> rx.Component:
    """Display market statistics and portfolio overview."""
    
    def stat_card(label: str, value: str, color: str = "inherit") -> rx.Component:
        """Create a statistics card."""
        return rx.vstack(
            rx.text(label, size="1", color="gray"),
            rx.text(value, size="2", weight="medium", color=color),
            spacing="0",
            align="start",
        )
    
    def index_display(index_code: str, index_name: str, current_value: float, change_percent: float) -> rx.Component:
        """Display a single market index."""
        color = rx.cond(change_percent >= 0, "#e74c3c", "#27ae60")
        return rx.hstack(
            rx.text(index_name, size="2", weight="medium"),
            rx.text(
                f"{current_value:.2f}",
                size="2",
                weight="bold",
                color=color,
            ),
            rx.text(
                f"{change_percent:.2f}%",
                size="2",
                weight="bold",
                color=color,
            ),
            spacing="2",
            align="baseline",
            on_click=lambda: PortfolioState.open_analysis_report(index_code),
            style={"cursor": "pointer", "_hover": {"opacity": "0.8"}},
        )
    
    return rx.vstack(
        # Title with icon - Total market value on left, Market indices in center, Total assets on right
        rx.hstack(
            rx.hstack(
                rx.text("总市值: ", size="2", color="gray"),
                rx.text(f"{PortfolioState.total_market_value:.2f}", size="4", color="red"),
                spacing="2",
                align="baseline",
            ),
            rx.spacer(),
            # Market indices in the center
            rx.hstack(
                rx.foreach(
                    PortfolioState.market_indices,
                    lambda idx: index_display(idx.index_code, idx.index_name, idx.current_value, idx.change_percent),
                ),
                spacing="4",
                justify="center",
                align="center",
            ),
            rx.spacer(),
            rx.hstack(
                rx.text("总资产: ", size="2", color="gray"),
                rx.text(f"{PortfolioState.total_assets:.2f}", size="4", color="red"),
                spacing="2",
                align="baseline",
            ),
            width="100%",
            align="center",
        ),
        # Main market value display
        #rx.hstack(
        #    rx.heading(
        #        f"{PortfolioState.total_market_value:.2f}",
        #        size="7",
        #        color="#e74c3c",
        #        style={"font_variant_numeric": "tabular-nums"},
        #    ),
        #    spacing="4",
        #    align="baseline",
        #),
        # Stats grid
        rx.grid(
            stat_card(
                "今日变化",
                f"{PortfolioState.today_change:.2f}({PortfolioState.today_change_percent:.2f}%)",
            ),
            stat_card(
                "浮动盘变化",
                f"{PortfolioState.float_change:.2f}({PortfolioState.float_change_percent:.2f}%)",
                color=rx.cond(PortfolioState.float_change >= 0, "#e74c3c", "#27ae60"), # type: ignore
            ),
            stat_card(
                "仓位",
                f"+{PortfolioState.position_percent:.2f}({PortfolioState.position_percent:.2f}%)",
            ),
            stat_card(
                "本金",
                f"{PortfolioState.principal:.2f}",
            ),
            stat_card(
                "可用",
                f"{PortfolioState.withdrawable:.2f}",
            ),
            stat_card(
                "可取",
                f"{PortfolioState.cash:.2f}",
            ),
            columns="6",
            spacing="7",
            width="100%",
        ),
        spacing="1",
        width="100%",
    )
