"""Sidebar component for portfolio page."""
import reflex as rx
from imobile.states.portfolio_state import PortfolioState


def sidebar() -> rx.Component:
    """Create a collapsible sidebar with navigation and theme toggle."""
    
    # Menu items
    menu_items = [
        {"icon": "home", "label": "首页"},
        {"icon": "trending-up", "label": "行情"},
        {"icon": "wallet", "label": "持仓"},
        {"icon": "bar-chart-3", "label": "交易"},
    ]
    
    def menu_button(icon: str, label: str) -> rx.Component:
        """Create a menu button with tooltip when collapsed."""
        is_active = PortfolioState.active_menu == label
        
        button_content = rx.button(
            rx.hstack(
                rx.icon(icon, size=20),
                rx.cond(
                    PortfolioState.is_sidebar_expanded,
                    rx.text(label, size="2", weight="medium"),
                    rx.fragment(),
                ),
                spacing="3",
                align="center",
            ),
            on_click=PortfolioState.set_active_menu(label),
            variant="ghost",
            width="100%",
            justify="start",
            padding="3",
            color_scheme=rx.cond(is_active, "blue", "gray"),
            style={
                "border_right": rx.cond(
                    is_active,
                    "2px solid var(--accent-9)",
                    "none",
                ),
            },
        )
        
        # Always wrap with tooltip - it will show on hover when collapsed
        return rx.tooltip(
            button_content,
            content=label,
            side="right",
        )
    
    # Theme toggle button with tooltip
    theme_button = rx.tooltip(
        rx.button(
            rx.hstack(
                rx.cond(
                    PortfolioState.is_dark_mode,
                    rx.icon("sun", size=20),
                    rx.icon("moon", size=20),
                ),
                rx.cond(
                    PortfolioState.is_sidebar_expanded,
                    rx.cond(
                        PortfolioState.is_dark_mode,
                        rx.text("浅色模式", size="2", weight="medium"),
                        rx.text("深色模式", size="2", weight="medium"),
                    ),
                    rx.fragment(),
                ),
                spacing="3",
                align="center",
            ),
            on_click=PortfolioState.toggle_theme,
            variant="ghost",
            width="100%",
            justify="start",
            padding="3",
        ),
        content="切换主题",
        side="right",
    )
    
    # Settings button with tooltip
    settings_button = rx.tooltip(
        rx.button(
            rx.hstack(
                rx.icon("settings", size=20),
                rx.cond(
                    PortfolioState.is_sidebar_expanded,
                    rx.text("设置", size="2", weight="medium"),
                    rx.fragment(),
                ),
                spacing="3",
                align="center",
            ),
            variant="ghost",
            width="100%",
            justify="start",
            padding="3",
        ),
        content="设置",
        side="right",
    )
    
    # Desktop toggle button (next to Home icon)
    desktop_toggle_button = rx.button(
        rx.icon(
            rx.cond(
                PortfolioState.is_sidebar_expanded,
                "chevron-left",
                "chevron-right",
            ),
            size=18,
        ),
        on_click=PortfolioState.toggle_sidebar,
        variant="ghost",
        size="1",
        padding="2",
        class_name="desktop-toggle-btn",
        style={
            "position": "absolute",
            "right": "-12px",
            "top": "12px",
            "border_radius": "50%",
            "background": "var(--color-panel)",
            "border": "1px solid var(--gray-5)",
            "z_index": "60",
        },
    )
    
    return rx.box(
        rx.vstack(
            # Header with toggle button (desktop only)
            rx.box(
                desktop_toggle_button,
                position="relative",
                width="100%",
                height="0",
            ),
            # Navigation items
            rx.vstack(
                *[menu_button(item["icon"], item["label"]) for item in menu_items],
                spacing="1",
                width="100%",
            ),
            # Settings section at bottom
            rx.spacer(),
            rx.divider(),
            theme_button,
            settings_button,
            spacing="4",
            height="100vh",
            padding="4",
        ),
        position="fixed",
        left="0",
        top="0",
        height="100vh",
        width=rx.cond(PortfolioState.is_sidebar_expanded, "14rem", "4rem"),
        border_right="1px solid var(--gray-5)",
        background="var(--color-panel)",
        transition="width 0.3s ease",
        z_index="50",
        # Hide on mobile by default, show when is_sidebar_visible_on_mobile is True
        class_name=rx.cond(
            PortfolioState.is_sidebar_visible_on_mobile,
            "sidebar-visible",
            "sidebar-mobile-hidden",
        ),
    )
