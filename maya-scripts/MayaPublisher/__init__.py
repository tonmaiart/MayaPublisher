try:
    from UkoreMenu import registry, MenuItemSpec, ReloadHandlerSpec, reload_package

    registry.register_item(
        MenuItemSpec(
            id="maya_publisher",
            label="Maya Publisher...",
            category="General",
            command="from tmlib.core import File; File.launch('MayaPublisher')",
            order=30,  # after Maya File Browser(10)/Ukore Reference Editor(20), divider follows
            divider_after=True,
        )
    )
    registry.register_reload_handler(
        ReloadHandlerSpec(
            id="maya_publisher",
            label="Maya Publisher",
            callback=lambda: reload_package("MayaPublisher"),
            order=50,
        )
    )
except ImportError:
    pass
