try:
    from UkoreMenu import registry, MenuItemSpec

    registry.register_item(
        MenuItemSpec(
            id="maya_publisher",
            label="Maya Publisher...",
            category="General",
            command="from tmlib.core import File; File.launch('MayaPublisher')",
            order=50,  # bottom of General — after Maya File Browser(10)/Save Increment(20)/Ukore Reference Editor(30)/Fix Sound Offset(40)
        )
    )
except ImportError:
    pass
