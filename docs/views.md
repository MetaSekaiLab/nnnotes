# Master data views

A view answers, for every row of a master table, which objects of the catalog the game shows for it: the full
illustration of a member card and its sub-sprites, the icon of a stamp, the jacket of a song, the banner of a story
episode, the icon of a reward. The rules are data (`src/nnnotes/viewrules.json`, package data) read by
`nnnotes.views`; a view document lists object ids, so it depends on the master data and the catalog, never on how
the objects are encoded.

## Rules

```json
{"format": 1,
 "resolvers": {"GameResourceType": {"1": {"name": "Item", "table": "MasterItem", "vars": {"path": "_imagePath"},
                                          "address": "{path}", "expect": "Sprite", "names": {"name": "_nameTextId"}}}},
 "views": {"cards": {"version": 1, "table": "MasterMemberCard",
                     "vars": {"asset": "_assetID", "rarity": "_rarity",
                              "band": ["_characterID", "MasterCharacter._bandID"]},
                     "names": {"name": "_nameTextID"},
                     "prefixes": ["MemberCard/"],
                     "roles": [{"role": "member_full", "address": "MemberCard/{asset}/member_full[member_full]",
                                "expect": "Sprite", "required": true},
                               {"role": "movie", "address": "MemberCard/{id}/movie/{part}",
                                "each": {"var": "part", "values": ["anime_part1", "anime_part2"]},
                                "when": {"field": "rarity", "op": "eq", "value": 4}}]}}}
```

- `format`: the rules format this version reads (1). Every view has a `version`; a change of a view's rule is a
  change of its version.
- `table`: the master table of the rows (the rows of a view are sorted by `id`, default `_id`). A view of
  `sources` instead has one row per distinct pair of a GameResourceType and an id that the listed tables name
  (`{table, type, id}`: the columns of the pair); its vars are `type` and `id`.
- `vars`: values of a row. A column name, or a chain `[column, "Table.column", ...]`: each step looks the previous
  value up as the `_id` of the next table.
- `names`: text ids (MasterText `_id`), given in all five languages (`ja`, `en`, `zh-Hant`, `zh-Hans`, `ko`); a text
  id without a MasterText row gives `null`, an empty text id leaves the name out.
- `prefixes`, `exclude`: the catalog keys the view accounts for (reverse coverage); `*` stands for one path segment.
- `roles`: `role` (its name), `address` (a template: `{var}` or `{var:spec}` with a Python format spec, such as
  `{id:05d}`), `expect` (`Sprite`, `Texture2D`, `GameObject`, another class name, a list of class names in order of
  preference, or `any`, the default), `required`, `when`, `each`, or `resolver` instead of an address (a resolver
  role has no `each` or `expect`: the resolver says what each code expects).
- `when`: a condition or a list of conditions that must all hold, `{field, op, value}`, where `field` is a var, an
  enumerated var or a column. Operators: `eq`, `ne`, `lt`, `le`, `gt`, `ge`, `in`, `notin`, `empty`, `nonempty`.
  A role whose condition fails is `not-applicable`.
- `each`: the role is a list, one entry per value of `var`: fixed `values`, the list in a `column`, or the distinct
  next path segments of the catalog keys under `catalogPrefix` (a template).
- `resolvers`: per name, per code, how a polymorphic reference is shown: the `table` whose row has that id, its
  `vars` and `names`, the `address` and `expect`. A code with no table has a fixed address.

## Resolving an address

An address is a catalog key, optionally followed by an Addressables sub-object name: `key[sub]`. The address index
maps each catalog key to the census objects of the container path it loads, in the order the bundle stores them
(object id, class, name, and the stable address of `contract.stable_address` when known);
`views.AddressIndex.from_addresses` builds it from the address table of the `link.addresses` stage.

A role resolves to the object the Addressables loader returns when the game loads the address as the expected class:

- With `[sub]`: the first object named `sub` of the expected class.
- Without `[sub]`: the first object of the expected class. A Sprite load of a sprite sheet (a texture with several
  sprites) returns its first sprite in stored order, whatever the sprites are named.
- With no expected class (`any`), the first object (for a texture, the texture itself). A list of classes tries them
  in order and takes the first class that has an object.

The entry of a role holds `address`, `status` and, when resolved, `object`, `class`, `stable` and, when several
objects fit, `among` (how many). Statuses:

| Status | Meaning | Also in the entry |
|---|---|---|
| `ok` | an object fits | `object`, `class`, `stable`, `among` |
| `missing-key` | not a catalog key, or a key whose container has no census object | `detail` |
| `missing-sub` | the key has objects, none fits | `available`: the key's objects |
| `not-applicable` | the condition of the role fails, or the resource type has no resolver | |
| `no-value` | a value the address needs is empty, or a chain or resolver finds no row | `detail` |

An entry of an `each` role also holds `each` (`{var: value}`); an entry of a resolver role holds `resource` (the
resolver's name).

## View documents

`nnnotes.view/1`, canonical JSON (docs/contracts.md):

```json
{"schema": "nnnotes.view/1", "view": "cards", "version": 1, "rules": "<digest>", "tables": ["MasterBand", "..."],
 "rows": [{"id": 1, "vars": {"asset": 1, "rarity": 2},
           "names": {"name": {"ja": "...", "en": "...", "zh-Hant": "...", "zh-Hans": "...", "ko": "..."}},
           "roles": {"member_full": {"address": "MemberCard/1/member_full[member_full]", "status": "ok",
                                     "object": "CAB-...:-4153...", "class": "Sprite"},
                     "movie": [{"each": {"part": "anime_part1"}, "status": "not-applicable"}]}}],
 "coverage": {"rows": 1,
              "roles": {"member_full": {"required": true, "counts": {"ok": 1}, "gaps": []}},
              "reverse": {"prefixes": ["MemberCard/"], "exclude": [], "keys": 1, "unreferenced": []}}}
```

- `rules`: the key hash of the rules format, the view's rule and the resolvers it uses (`views.rules_digest`);
  `tables`: every master table the view reads.
- Rows of a view of sources also hold `from`, the tables that name the resource.
- Coverage both ways. Forward, per role: the number of entries per status, and `gaps`, the rows where a required
  role is `missing-key` or `missing-sub` (`no-value` and `not-applicable` are the master data saying there is
  nothing to show). Reverse: the catalog keys under the prefixes that no row references.
  `views.unreferenced` does the same over several views, so views sharing a folder cover each other. Gaps are data:
  building a view never fails because of them.
- A view reads the master tables it lists, its rules and part of the address index. `views.Recorder` records that
  part (the keys looked up and the prefixes listed) as a document whose digest identifies it: the same tables, rules
  and recorded part give the same view.
- Object ids change when a bundle's content changes; `views.diff` compares two documents of a view per row, role and
  enumerated value, and tells a new status or address from a new place (`moved`: the stable address changed) and
  from a new object id at the same place (`object`).

## Stages

`nnnotes.views:stages` gives one stage per view, `view.<name>` (docs/stages.md), run by `nnnotes export --views`
and planned by `nnnotes plan`:

- Subjects: the catalog subjects of `link.addresses` (the export command has one, `main`), when master data is set
  and the view is selected (the planning facts `master` and `views`).
- Inputs: `master:<Table>` for every table the view reads (the decoded table file), `rules` (the rules document of
  the view alone: its rule, the resolvers it uses and the format) and `addresses` (the part of the address table
  of `link.addresses:<subject>` the view reads: the keys it looked up and the prefixes it listed, as
  `views.Recorder` records it). No parameters, no atoms. So the key of a view changes when one of its tables, its
  rule or the addresses it reads change, and never with how objects are encoded or with tables and keys other
  views read. A table the master data lacks fails the task.
- Version: the view's `version`.
- Artifact: `view.<name>:<subject>#view`, the view document; its facts: `rows`, `entries` (entries per status),
  `gaps` (as `views.gaps`: the (row, required role) pairs that are `missing-key` or `missing-sub`; `export
  --strict` exits 1 when a view has any) and `unreferenced` (reverse coverage).
- Derived document of the `original` layout: `views/<name>.json` (`views/<subject>/<name>.json` for a subject other
  than `main`), the view document with `"layout": "original"` and, in every entry naming an object, `files`
  (artifact role -> path of the artifacts the layout places for the object; for an object inside a prefab, the
  prefab's file) and `path` (the file of its primary artifact; `null` when the layout places none, for instance
  when the object's bundle was not exported).

## Views
### `backgrounds`

Profile backgrounds: the rows store full addresses. Table `MasterBackground`. Vars: `path` = `_assetPath`, `thumbnail`
= `_thumbnailAssetPath`. Names: `name` = `_nameTextId`, `description` = `_descriptionTextId`. Prefixes:
`Image/Background/`, `thumbnail/Background/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `image` | `{path}` | any |  | yes |
| `thumbnail` | `{thumbnail}` | Sprite |  | yes |

### `band_items`

Band items. Table `MasterBandItem`. Vars: `id` = `_id`, `band` = `_bandId`. Names: `name` = `_nameTextId`,
`description` = `_descriptionTextId`. Prefixes: `Band/*/BandItem/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `band_item` | `Band/{band}/BandItem/{id}/band_item` | Sprite |  | yes |

### `bands`

Bands. Table `MasterBand`. Vars: `id` = `_id`, `r_background` = `_memberRarityRBackgroundAssetPath`. Names: `name` =
`_nameTextID`. Prefixes: `Band/` (except `Band/*/BandItem/`).

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `band_small_icon` | `Band/{id}/band_small_Icon` | Sprite |  |  |
| `band_small_icon_white` | `Band/{id}/band_small_icon_white` | Sprite |  |  |
| `band_room_background` | `Band/{id}/band_room_background` | Sprite |  |  |
| `band_board_self` | `Band/{id}/Friendship/BandBoard_Self` | GameObject |  |  |
| `band_board_other` | `Band/{id}/Friendship/BandBoard_Other` | GameObject |  |  |
| `band_logo` | `Band/{id}/band_logo` | Sprite |  |  |
| `band_logo_white` | `Band/{id}/band_logo_white` | Sprite |  |  |
| `band_menu_enabled_icon` | `Band/{id}/band_menu_enabled_icon` | Sprite |  |  |
| `band_menu_disabled_icon` | `Band/{id}/band_menu_disabled_icon` | Sprite |  |  |
| `live_stage` | `Band/{id}/live_stage/live_stage` | any |  |  |
| `live_stage_lightweight_background` | `Band/{id}/live_stage/lightweight_background` | Sprite |  |  |
| `live_start_timeline` | `Band/{id}/timeline/live_start_playable_timeline` | any |  |  |
| `band_profile` | `Band/{id}/BandProfile/BandProfile` | any |  |  |
| `band_fade` | `Band/{id}/band_fade` | Sprite |  |  |
| `band_studio_background` | `Band/{id}/band_studio_background` | Sprite |  |  |
| `band_stage_background` | `Band/{id}/band_stage_background` | Sprite |  |  |
| `band_arena_background` | `Band/{id}/band_arena_background` | Sprite |  |  |
| `login_bonus_bg` | `Band/{id}/LoginBonus/LoginBonusBg` | any |  |  |
| `login_bonus_screen_bg` | `Band/{id}/LoginBonus/LoginBonusScreenBg` | any |  |  |
| `login_bonus_screen_frame` | `Band/{id}/LoginBonus/LoginBonusScreenFrame` | any |  |  |
| `member_rarity_r_background` | `{r_background}` | Sprite |  |  |

### `cards`

Member cards. `asset` formats most addresses; the movie parts use the card id. R cards (rarity 2) show the background
of their character's band; the movie parts are those of SSR cards (rarity 4). Table `MasterMemberCard`. Vars: `id` =
`_id`, `asset` = `_assetID`, `rarity` = `_rarity`, `band` = `_characterID` → `MasterCharacter._bandID`, `r_background`
= `_characterID` → `MasterCharacter._bandID` → `MasterBand._memberRarityRBackgroundAssetPath`. Names: `name` =
`_nameTextID`, `subtitle` = `_subtitleTextID`. Prefixes: `MemberCard/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `member_full` | `MemberCard/{asset}/member_full[member_full]` | Sprite |  | yes |
| `member_full_detail_pivot` | `MemberCard/{asset}/member_full[detail_pivot]` | Sprite |  |  |
| `member_full_face_center_x` | `MemberCard/{asset}/member_full[face_center_x]` | Sprite |  |  |
| `member_full_face_center` | `MemberCard/{asset}/member_full[face_center]` | Sprite |  |  |
| `member_full_sprite_meta_data` | `MemberCard/{asset}/member_full_sprite_meta_data` | any |  |  |
| `member_character` | `MemberCard/{asset}/member_character[main]` | Sprite |  | yes |
| `member_character_formation` | `MemberCard/{asset}/member_character[formation]` | Sprite |  |  |
| `member_character_member_result` | `MemberCard/{asset}/member_character[member_result]` | Sprite |  |  |
| `member_character_face_center_x` | `MemberCard/{asset}/member_character[face_center_x]` | Sprite |  |  |
| `member_character_face_center` | `MemberCard/{asset}/member_character[face_center]` | Sprite |  |  |
| `member_character_sprite_meta_data` | `MemberCard/{asset}/member_character_sprite_meta_data` | any |  |  |
| `member_background` | `MemberCard/{asset}/member_background[member_background]` | Sprite | `rarity` ne 2 |  |
| `member_background_formation` | `MemberCard/{asset}/member_background[formation]` | Sprite | `rarity` ne 2 |  |
| `member_background_texture` | `MemberCard/{asset}/member_background` | Texture2D |  |  |
| `member_background_r` | `{r_background}` | Sprite | `rarity` eq 2 |  |
| `gacha_band_background` | `MemberCard/MemberCommon/member_background{band}[member_background{band}_0]` | Sprite |  |  |
| `member_thumbnail` | `MemberCard/{asset}/member_thumbnail[member_thumbnail]` | Sprite |  | yes |
| `member_thumbnail_square` | `MemberCard/{asset}/member_thumbnail[square]` | Sprite |  | yes |
| `skill_sprite` | `MemberCard/{asset}/skill_sprite` | Sprite |  |  |
| `member_preview_movie` | `MemberCard/{asset}/member_preview_movie` | any |  |  |
| `movie` | `MemberCard/{id}/movie/{part}` for each `part` in `anime_part1`, `anime_part2`, `live2d_in`, `live2d_loop` | any | `rarity` eq 4 |  |
| `member_gacha_animation_config` | `MemberCard/{asset}/member_gacha_animation_config` | any |  |  |

### `characters`

Characters. Table `MasterCharacter`. Vars: `id` = `_id`. Names: `name` = `_nameTextID`, `short_name` =
`_shortNameTextID`. Prefixes: `Character/Image/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `character_sprite` | `Character/Image/{id}/character_sprite` | Sprite |  |  |
| `character_thumbnail` | `Character/Image/{id}/character_thumbnail` | Sprite |  |  |
| `character_face_icon` | `Character/Image/{id}/character_face_icon` | Sprite |  |  |
| `character_round_icon` | `Character/Image/{id}/character_round_icon` | Sprite |  |  |
| `board_icon` | `Character/Image/{id}/board_icon` | Sprite |  |  |

### `comics`

Loading screen comics. Table `MasterLoadingComics`. Vars: `image` = `_imageAsset`. Prefixes: `Image/Comic/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `comic` | `Image/Comic/{image}` | Sprite |  | yes |

### `degrees`

Degrees (titles): the row stores the full address. Table `MasterDegree`. Vars: `path` = `_imagePath`. Names: `name` =
`_nameTextId`, `description` = `_descriptionTextId`. Prefixes: `Image/Degree/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `image` | `{path}` | Sprite |  | yes |

### `events`

Events. Table `MasterEvent`. Vars: `image` = `_imageAsset`, `logo` = `_logoAsset`, `background` = `_backgroundAsset`,
`banner` = `_bannerAsset`. Names: `name` = `_nameTextId`. Prefixes: `Image/Event/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `image` | `Image/Event/{image}` | Sprite |  | yes |
| `logo` | `Image/Event/{logo}` | Sprite |  | yes |
| `background` | `Image/Event/{background}` | Sprite |  | yes |
| `banner` | `Image/Event/{banner}` | Sprite |  | yes |

### `exchange_categories`

Exchange categories. Table `MasterExchangeCategory`. Vars: `banner` = `_bannerAsset`. Names: `name` = `_nameTextId`.
Prefixes: `Exchange/Category/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `banner` | `Exchange/Category/{banner}` | Sprite |  | yes |

### `exchange_products`

Exchange products. Table `MasterExchangeProduct`. Vars: `thumbnail` = `_thumbnailAsset`. Prefixes:
`Exchange/ItemThumbnail/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `thumbnail` | `Exchange/ItemThumbnail/{thumbnail}` | Sprite |  | yes |

### `friendship_episodes`

Friendship story episodes (their banners share a folder with the story episodes'). Table
`MasterStoryFriendshipEpisode`. Vars: `banner` = `_banner`. Prefixes: `Story/Banner/Episode/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `banner` | `Story/Banner/Episode/{banner}` | Sprite |  | yes |

### `friendships`

Character pairs of friendship stories. Table `MasterCharacterFriendship`. Vars: `banner` = `_storyBanner`. Prefixes:
`Character/StoryBanner/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `story_banner` | `Character/StoryBanner/{banner}` | Sprite |  | yes |

### `gacha`

Gacha banners and logos. The game formats the logo address from the gacha id; the row also stores a logo address.
Table `MasterGacha`. Vars: `id` = `_id`, `banner` = `_bannerAssetName`, `logo_asset` = `_logoAssetName`. Names: `name`
= `_nameTextId`. Prefixes: `Gacha/Banner/`, `Gacha/Logo/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `banner` | `{banner}` | Sprite |  | yes |
| `logo` | `Gacha/Logo/gacha_logo_{id}` | Sprite |  |  |
| `logo_asset` | `{logo_asset}` | Sprite |  |  |

### `home_banners`

Home screen banners: the row stores the full address. Table `MasterHomeBanner`. Vars: `image` = `_imageAsset`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `image` | `{image}` | Sprite |  | yes |

### `items`

Items: the row stores the full address. Table `MasterItem`. Vars: `path` = `_imagePath`. Names: `name` =
`_nameTextId`, `description` = `_descriptionTextId`. Prefixes: `Item/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `icon` | `{path}` | Sprite |  | yes |

### `jackets`

Song jackets. Table `MasterLiveMusic`. Vars: `jacket` = `_jacketAssetName`. Names: `title` = `_titleTextID`. Prefixes:
`Image/Jacket/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `jacket` | `Image/Jacket/{jacket}` | Sprite |  | yes |
| `jacket_small` | `Image/Jacket/small/{jacket}` | Sprite |  | yes |

### `login_bonuses`

Login bonus sheets: the rows store full addresses. Table `MasterLoginBonus`. Vars: `sheet` = `_sheetImageAsset`,
`background` = `_backgroundImageAsset`. Names: `name` = `_nameTextID`. Prefixes: `LoginBonus/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `sheet` | `{sheet}` | Sprite |  | yes |
| `background` | `{background}` | Sprite |  | yes |

### `monthly_passes`

Monthly passes (the id written with five digits). Table `MasterMonthlyPass`. Vars: `id` = `_id`. Names: `name` =
`_nameTextId`, `description` = `_descriptionTextId`. Prefixes: `Shop/Pass/Banner/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `banner` | `Shop/Pass/Banner/{id:05d}` | Sprite |  | yes |

### `rewards`

Every resource that a reward, prize, product or payment table names: one row per distinct (GameResourceType, id),
`from` lists the tables naming it, and the icon comes from the resolver of the type. Sources (type, id):
`MasterArenaDailyReward`, `MasterArenaPromotionReward`, `MasterArenaRankingReward`, `MasterArenaSeasonReward`,
`MasterBattleLiveReward`, `MasterChallengeLiveEventReward`, `MasterCharacterFriendshipRankReward`,
`MasterCharacterRankReward`, `MasterCircleMissionReward`, `MasterExchange`, `MasterExchangeProduct`,
`MasterGachaBonusLot`, `MasterGachaPrize`, `MasterGekisouLiveRankReward`, `MasterInvitationReward`,
`MasterInvitationSuccessReward`, `MasterLiveArenaRankReward`, `MasterLiveEventReward`, `MasterLiveFreeReward`,
`MasterLiveMusicComboReward`, `MasterLiveMusicScoreReward`, `MasterLiveStamp`, `MasterLoginBonusSlot`,
`MasterMissionReward`, `MasterMonthlyPassContinuationReward`, `MasterMonthlyPassDailyReward`,
`MasterMonthlyPassFirstTimeReward`, `MasterMonthlyPassPurchaseReward`, `MasterOfflineBonusItemLot`, `MasterReward`,
`MasterSeasonPassReward`, `MasterShopProduct`, `MasterStoryReward`, `MasterVipDailyReward`, `MasterVipRankPack`,
`MasterVipRankUpReward`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `icon` | resolver `GameResourceType` | per type |  | yes |

### `season_passes`

Season passes. Table `MasterSeasonPass`. Vars: `banner` = `_bannerAsset`. Names: `name` = `_nameTextId`, `description`
= `_descriptionTextId`. Prefixes: `SeasonPass/Banner/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `banner` | `SeasonPass/Banner/{banner}` | Sprite |  | yes |

### `shops`

Shop products. Table `MasterShop`. Vars: `thumbnail` = `_thumbnailAsset`, `dialog` = `_dialogAsset`, `tab` =
`_tabAsset`. Names: `name` = `_nameTextId`, `description` = `_descriptionTextId`. Prefixes: `Shop/ItemThumbnail/`,
`Shop/Dialog/`, `Shop/Tab/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `thumbnail` | `Shop/ItemThumbnail/{thumbnail}` | Sprite |  | yes |
| `dialog` | `Shop/Dialog/{dialog}` | Sprite |  | yes |
| `tab` | `Shop/Tab/{tab}` | Sprite |  | yes |

### `skill_icons`

Skill icons (a skill names its icon by id). Table `MasterSkillIcon`. Vars: `icon` = `_normalIconAssetName`. Prefixes:
`Character/Skill/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `icon` | `Character/Skill/{icon}` | Sprite |  | yes |

### `spots`

Home spots: the rows store full addresses. Table `MasterHomeSpot`. Vars: `thumbnail` = `_thumbnailAssetPath`,
`background` = `_backgroundAssetPath`, `situation` = `_situationAssetPath`. Names: `name` = `_nameTextId`. Prefixes:
`Image/Spot/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `thumbnail` | `{thumbnail}` | Sprite |  | yes |
| `background` | `{background}` | any |  |  |
| `situation` | `{situation}` | any |  |  |

### `stamps`

Stamps: the row stores the full address. Table `MasterStamp`. Vars: `path` = `_stampAsset`. Names: `name` =
`_nameTextId`. Prefixes: `Stamp/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `stamp` | `{path}` | Sprite |  | yes |

### `story_chapters`

Story chapters. Table `MasterStoryChapter`. Vars: `banner` = `_banner`, `image` = `_image`, `icon` = `_icon`. Names:
`name` = `_nameTextId`, `description` = `_descriptionTextId`. Prefixes: `Story/Banner/Chapter/`,
`Story/Image/Chapter/`, `Story/Icon/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `banner` | `Story/Banner/Chapter/{banner}` | Sprite |  | yes |
| `image` | `Story/Image/Chapter/{image}` | Sprite |  | yes |
| `icon` | `Story/Icon/{icon}` | Sprite |  | yes |

### `story_episodes`

Story episodes. Table `MasterStoryEpisode`. Vars: `banner` = `_banner`, `image` = `_image`. Names: `description` =
`_descriptionTextId`. Prefixes: `Story/Banner/Episode/`, `Story/Image/Episode/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `banner` | `Story/Banner/Episode/{banner}` | Sprite |  | yes |
| `image` | `Story/Image/Episode/{image}` | Sprite |  | yes |

### `support_cards`

Support cards. Table `MasterSupportCard`. Vars: `asset` = `_assetID`. Names: `name` = `_nameTextID`, `description` =
`_descriptionTextID`. Prefixes: `SupportCard/`.

| Role | Address | Expect | Condition | Required |
|---|---|---|---|---|
| `snap_full` | `SupportCard/{asset}/snap_full[snap_full]` | Sprite |  | yes |
| `snap_thumbnail` | `SupportCard/{asset}/snap_thumbnail` | Sprite |  | yes |
| `skill_sprite` | `SupportCard/{asset}/skill_sprite` | Sprite |  |  |

## Resolvers

`GameResourceType` (the icon of a resource, as the game's resource icon shows it; a type without a row here has no
icon: `not-applicable`):

| Code | Resource | Table | Address | Expect |
|---|---|---|---|---|
| 1 | Item | `MasterItem` | `{_imagePath}` | Sprite |
| 2 | MemberCard | `MasterMemberCard` | `MemberCard/{_assetID}/member_thumbnail[member_thumbnail]` | Sprite |
| 3 | SupportCard | `MasterSupportCard` | `SupportCard/{_assetID}/snap_thumbnail` | Sprite |
| 7 | GachaPoint |  | `Item/common/item_icon_seal_member` | Sprite |
| 8 | Music | `MasterLiveMusic` | `Image/Jacket/{_jacketAssetName}` | Sprite |
| 9 | Stamp | `MasterStamp` | `{_stampAsset}` | Sprite |
| 10 | PremiumPass | `MasterSeasonPass` | `SeasonPass/Banner/{_bannerAsset}` | Sprite |
| 17 | Degree | `MasterDegree` | `{_imagePath}` | Sprite |
| 18 | Background | `MasterBackground` | `{_thumbnailAssetPath}` | Sprite |
| 19 | Spot | `MasterHomeSpot` | `{_thumbnailAssetPath}` | Sprite |
| 1001 | BiliChatTheme | `MasterBiliChatTheme` | `{_iconAssetPath}` | Sprite |
| 1002 | BiliChatBubble | `MasterBiliChatBubble` | `{_iconAssetPath}` | Sprite |
| 1003 | BiliChatFrame | `MasterBiliChatFrame` | `{_iconAssetPath}` | Sprite |
