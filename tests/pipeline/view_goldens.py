"""Master-view golden inputs with localized rows and partial address coverage."""

from nnnotes.pipeline import contract, views, voices
from nnnotes.pipeline.stages import describe
from tests.pipeline import test_views, test_voices
from tests.pipeline.product_goldens import provider, stored, task

ROWS = {
    "MasterSupportCard": {"_assetID": 1},
    "MasterBandItem": {"_bandId": 1},
    "MasterStamp": {"_stampAsset": "Stamp/s1"},
    "MasterItem": {"_imagePath": "Item/one"},
    "MasterDegree": {"_imagePath": "Image/Degree/one"},
    "MasterBackground": {"_assetPath": "Image/Background/one", "_thumbnailAssetPath": "thumbnail/Background/one"},
    "MasterHomeSpot": {
        "_thumbnailAssetPath": "Image/Spot/one",
        "_backgroundAssetPath": "Spot/one/background",
        "_situationAssetPath": "Spot/one/day",
    },
    "MasterLiveMusic": {"_jacketAssetName": "one", "_titleTextID": 1},
    "MasterGacha": {"_bannerAssetName": "Gacha/Banner/one", "_logoAssetName": "Gacha/Logo/one"},
    "MasterHomeBanner": {"_imageAsset": "Home/Banner/one"},
    "MasterLoginBonus": {
        "_sheetImageAsset": "LoginBonus/one/sheet",
        "_backgroundImageAsset": "LoginBonus/one/background",
    },
    "MasterStoryChapter": {"_banner": "one", "_image": "one", "_icon": "one"},
    "MasterStoryEpisode": {"_banner": "one", "_image": "one"},
    "MasterStoryFriendshipEpisode": {"_banner": "one"},
    "MasterCharacterFriendship": {"_storyBanner": "one"},
    "MasterEvent": {
        "_imageAsset": "image",
        "_logoAsset": "logo",
        "_backgroundAsset": "background",
        "_bannerAsset": "banner",
    },
    "MasterShop": {"_thumbnailAsset": "one", "_dialogAsset": "one", "_tabAsset": "one"},
    "MasterMonthlyPass": {},
    "MasterSeasonPass": {"_bannerAsset": "one"},
    "MasterExchangeCategory": {"_bannerAsset": "one"},
    "MasterExchangeProduct": {"_thumbnailAsset": "one"},
    "MasterLoadingComics": {"_imageAsset": "one"},
    "MasterSkillIcon": {"_normalIconAssetName": "one"},
    "MasterArenaDailyReward": {"_resourceType": 1, "_resourceId": 1},
}


def view_provider(name):
    stage = views.ViewStage(name, views.load_rules())

    def build(depot):
        tables = {table: [] for table in stage.tables}
        tables.update(test_views.CARD_TABLES)
        tables.update(
            {
                table: [{"_id": 1, "_nameTextId": 1, "_nameTextID": 1, "_descriptionTextId": 1, **row}]
                for table, row in ROWS.items()
            }
        )
        tables["MasterText"] = [*tables["MasterText"], test_views.text(1, "Example")]
        entries = {
            **test_views.card_entries(),
            "Stamp/s1": [test_views.o(95, "Sprite", "s1")],
            "Item/one": [test_views.o(96, "Sprite", "item")],
            "SupportCard/1/snap_full": [test_views.o(97, "Sprite", "snap_full")],
        }
        recorder = views.Recorder(views.AddressIndex(entries))
        views.build(stage.rules, name, tables, recorder)
        inputs = [
            stored(depot, contract.encode({"_allData": tables[table]}), "master:" + table) for table in stage.tables
        ]
        inputs += [
            stored(depot, contract.encode(recorder.subset()), "addresses"),
            stored(depot, stage.rules_bytes, "rules"),
        ]
        return task(stage, "main", inputs)

    return provider(stage, build)


def voice_index():
    stage = voices.VoiceStage()

    def build(depot):
        env = test_voices.planning(depot, depot.root / "fixtures")
        described = describe(stage, "main", None, env)
        inputs = []
        for inp in described.inputs:
            data = depot.input_bytes(inp)
            if inp.role.startswith("master:"):
                data = contract.encode(contract.loads(data))
            inputs.append(stored(depot, data, inp.role))
        return contract.Task(
            stage.name, stage.version, "main", described.params, described.atoms, tuple(inputs), described.context
        )

    return provider(stage, build)


def view_backgrounds():
    return view_provider("backgrounds")


def view_band_items():
    return view_provider("band_items")


def view_bands():
    return view_provider("bands")


def view_cards():
    return view_provider("cards")


def view_characters():
    return view_provider("characters")


def view_comics():
    return view_provider("comics")


def view_degrees():
    return view_provider("degrees")


def view_events():
    return view_provider("events")


def view_exchange_categories():
    return view_provider("exchange_categories")


def view_exchange_products():
    return view_provider("exchange_products")


def view_friendship_episodes():
    return view_provider("friendship_episodes")


def view_friendships():
    return view_provider("friendships")


def view_gacha():
    return view_provider("gacha")


def view_home_banners():
    return view_provider("home_banners")


def view_items():
    return view_provider("items")


def view_jackets():
    return view_provider("jackets")


def view_login_bonuses():
    return view_provider("login_bonuses")


def view_monthly_passes():
    return view_provider("monthly_passes")


def view_rewards():
    return view_provider("rewards")


def view_season_passes():
    return view_provider("season_passes")


def view_shops():
    return view_provider("shops")


def view_skill_icons():
    return view_provider("skill_icons")


def view_spots():
    return view_provider("spots")


def view_stamps():
    return view_provider("stamps")


def view_story_chapters():
    return view_provider("story_chapters")


def view_story_episodes():
    return view_provider("story_episodes")


def view_support_cards():
    return view_provider("support_cards")
