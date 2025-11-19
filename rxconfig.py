import reflex as rx

config = rx.Config(
    app_name="imobile",
    db_url="sqlite:///db/imobile.db",
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
    ]
)
