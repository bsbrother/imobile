"""Market statistics component."""
import reflex as rx
from imobile.states.portfolio_state import PortfolioState


def market_stats() -> rx.Component:
    """Display market statistics and portfolio overview."""
    
    def stat_card(label: str, value: str, color: str = "inherit") -> rx.Component:
        """Create a statistics card."""
        return rx.vstack(
            rx.text(label, size="1", color="gray"),
            rx.text(value, size="1", weight="medium", color=color),
            spacing="1",
            align="start",
        )
    
    return rx.vstack(
        # Title with icon - Total market value on left, Total assets on right
        rx.hstack(
            rx.hstack(
                rx.text("总市值: ", size="2", color="gray"),
                rx.text(f"{PortfolioState.total_market_value:.2f}", size="4", color="red"),
                spacing="2",
                align="baseline",
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
                f"+{PortfolioState.float_change:.2f}(+{PortfolioState.float_change_percent:.2f}%)",
                color="#e74c3c",
            ),
            stat_card(
                "累计变化",
                f"+{PortfolioState.cumulative_change:.2f}(+{PortfolioState.cumulative_change_percent:.2f}%)",
                color="#e74c3c",
            ),
            stat_card(
                "现金",
                f"{PortfolioState.cash:.2f}",
            ),
            stat_card(
                "本金",
                f"{PortfolioState.principal:.2f}",
            ),
            columns="5",
            spacing="6",
            width="100%",
        ),
        spacing="1",
        width="100%",
    )
