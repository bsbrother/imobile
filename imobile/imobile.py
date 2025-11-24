"""iMobile - Stock Portfolio Tracking Application."""

import reflex as rx
from imobile import db  # Import db to register models
from imobile.pages.portfolio import portfolio
from imobile.pages.sector_history import sector_history


class State(rx.State):
    """The app state."""


def index() -> rx.Component:
    """Welcome page with link to portfolio."""
    return rx.container(
        rx.color_mode.button(position="top-right"),
        rx.vstack(
            rx.heading("欢迎使用 iMobile", size="9"),
            rx.text(
                "股票投资组合跟踪系统",
                size="5",
                color="gray",
            ),
            rx.link(
                rx.button("进入投资组合", size="3"),
                href="/portfolio",
            ),
            rx.link(
                rx.button("板块历史分析", size="3", variant="surface"),
                href="/sector-history",
            ),
            rx.link(
                rx.button("查看文档", variant="soft", size="3"),
                href="https://reflex.dev/docs/getting-started/introduction/",
                is_external=True,
            ),
            spacing="5",
            justify="center",
            align="center",
            min_height="85vh",
        ),
    )


# Create the app
app = rx.App(
    theme=rx.theme(
        appearance="dark",
        has_background=True,
        radius="large",
        accent_color="blue",
    ),
)

# Add pages
app.add_page(index, route="/", title="iMobile - 首页")
app.add_page(portfolio, route="/portfolio", title="iMobile - 投资组合")
app.add_page(sector_history, route="/sector-history", title="iMobile - Sector History")
