"""Portfolio page - main view for stock portfolio tracking."""
import reflex as rx
from imobile.components.sidebar import sidebar
from imobile.components.market_stats import market_stats
from imobile.components.stock_table import stock_table
from imobile.states.portfolio_state import PortfolioState


def portfolio() -> rx.Component:
    """Portfolio page component."""
    return rx.fragment(
        # Mobile hamburger menu button
        rx.button(
            rx.icon("menu", size=24),
            on_click=PortfolioState.toggle_mobile_sidebar,
            position="fixed",
            top="4",
            left="4",
            z_index="60",
            variant="soft",
            class_name="mobile-menu-btn",
            display=["block", "block", "none"],  # Show on mobile/tablet, hide on desktop
        ),
        
        # Mobile sidebar overlay
        rx.cond(
            PortfolioState.is_sidebar_visible_on_mobile,
            rx.box(
                on_click=PortfolioState.close_mobile_sidebar,
                class_name="sidebar-overlay",
            ),
            rx.fragment(),
        ),
        
        # Sidebar
        sidebar(),
        
        # Main content area
        rx.box(
            rx.container(
                # Loading indicator
                rx.cond(
                    PortfolioState.is_loading,
                    rx.center(
                        rx.spinner(size="3"),
                        padding="8",
                    ),
                    rx.vstack(
                        # Market stats section
                        market_stats(),
                        
                        # Stock table section
                        stock_table(),
                        
                        spacing="3",
                        width="100%",
                    ),
                ),
                padding="4",
                max_width="90rem",
                style={
                    "margin": "0 auto",  # Center the container
                },
            ),
            # Responsive margin based on sidebar width and screen size
            margin_left=[
                "0",  # mobile: no margin
                "0",  # tablet: no margin
                rx.cond(PortfolioState.is_sidebar_expanded, "14rem", "4rem"),  # desktop
            ],
            transition="margin-left 0.3s ease",
            width="100%",
            height="100vh",
            overflow_y="auto",
            overflow_x="hidden",
            class_name="main-content",
        ),
        on_mount=PortfolioState.on_load,  # Load data when page mounts
    )
