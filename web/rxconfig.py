import reflex as rx

config = rx.Config(
    app_name="app",
    db_url="sqlite:///../shared/db/imobile.db",
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(
                appearance="dark",
                has_background=True,
                radius="large",
                accent_color="blue",
            ),
        ),
    ],
)
