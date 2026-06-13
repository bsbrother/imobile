import reflex as rx

config = rx.Config(
    app_name="web.app.imobile",
    db_url="sqlite:///../shared/db/imobile.db",
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
    ]
)
