{
    "name": "GigaClub Minecraft Player Stats",
    "version": "16.0.1.0.0",
    "category": "GigaClub",
    "author": "GigaClub",
    "website": "https://github.com/GigaClub/gigaclub_odoo",
    "license": "AGPL-3",
    "depends": ["gigaclub_base"],
    "data": [
        "data/gc_minecraft_stats_data.xml",
        "security/ir.model.access.csv",
        "views/menu_views.xml",
        "views/gc_minecraft_player_stats_views.xml",
        "views/gc_minecraft_server_views.xml",
        "views/gc_minecraft_stats_views.xml",
        "views/gc_user_views.xml",
    ],
    "demo": [],
    "installable": True,
    "auto_install": False,
}