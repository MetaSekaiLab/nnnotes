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

## Voices

`view.voices` (`nnnotes.voices`) indexes the character voices the master data names and those of the story
episodes: per voice its source row, characters, category, text in the five languages and conditions, its cue sheet
and cue, and the decoded streams of the export that play it. It is a stage of its own rather than a view of the
rules above: besides master tables and the address table it reads the results of `unity.export` (the episodes) and
`cri.audio`, and the ACB of every decoded cue sheet. Its rules are the `voices` entry of `viewrules.json`
(`nnnotes voices` queries its document, docs/commands.md).

### Sources

| Source | Table | Characters, sound, text | Category |
|---|---|---|---|
| `Talk` | `MasterTalk` | `_characterId`, `_voiceSoundId`, `_textId` | `_category` (TalkCategory) |
| `CharacterVoice` | `MasterCharacterVoice` | `_characterId`, `_soundId`, `_textId` | `_type` (CharacterVoiceType) |
| `MemberCard` | `MasterMemberCard` | `_characterID`, `_gachaVoiceSoundId`, `_gachaVoiceTextId` | |
| `LiveCharacter` | `MasterLiveCharacter` | `_characterID`, `_liveSkillVoiceSoundID`, `_liveSkillVoiceTextID` | |
| `LiveGekisouVoice` | `MasterLiveGekisouVoice` | `_characterID`, `_voiceID`, `_voiceTextID` | `_gekisouVoiceType` (GekisouVoiceType) |
| `LiveDialogueCommon` | `MasterLiveDialogueCommon` | `_characterID`, `_comboVoiceSoundID`, `_comboVoiceTextID` | |
| `LiveDialogueFixedPair` | `MasterLiveDialogueFixedPair` | slot 1: `_characterID01`, `_character01ComboVoiceSoundID`, `_character01ComboVoiceTextID`; slot 2: the same with `02` | |
| `LiveStartCharacterVoice` | `MasterLiveStartCharacterVoice` | `_characterId`, `_voiceSoundId`, `_voiceTextId` | |
| `HomeSpot` | `MasterHomeSpot` | `_characterIds`, `_introSoundId` | |
| `Title` | `MasterTitle` | `_voiceCharacterIds`; cue names built per character (below) | the cue's name |
| `Sound` | `MasterSound` | the rows of category Voice that no other source names | |
| `Story` | `MasterAdv` | per voice id of an episode command: the speaker (inferred characters), the episode's sound, the line (below) | the episode's kind |

- The first eight are the tables of the game's voice collection; `source.type` gives their CharacterVoiceMasterType
  value. A row whose sound column is empty or 0 names no voice (counted as `empty`). Conditions are the listed
  columns with a value: `_costumeId`, `_year`, `_seasonStartAt`, `_seasonEndAt`, `_startAt`, `_birthdayCharacterId`,
  `_unlockCharacterRank` (Talk), `_scoreRank`, `_startAt` (CharacterVoice), `_startAt` (MemberCard, HomeSpot),
  `_dialogueType`, `_dialogueCallType` (LiveDialogueCommon), `_dialogueType`, `_unlockFriendshipRank`
  (LiveDialogueFixedPair), `_unlockCharacterRank` (LiveStartCharacterVoice).
- A category without an enum is named after the source. TalkCategory: None 0, CharacterRankUp 1, Season 2,
  Birthday 3, LoginBonus 4, Exchange 5, Live 6, OfflineBonus 7. CharacterVoiceType: LevelUp 0, Awaken 1,
  SkillLevelUp 2, RankUp 3, LiveClear 4, LiveFullCombo 5, LiveAllPerfect 6, LiveResult 7, LiveBattleResultFirst 8,
  LiveBattleResultHigh 9, LiveBattleResultLow 10. GekisouVoiceType: StartCombo 0, StartJustCount 1, StartLuck 2,
  TopRank 3. MasterSound `_category`: Bgm 0, Se 1, Voice 2, NotesSe 3.
- `Title`: every character in some row's `_voiceCharacterIds` (`source.rows`: those rows), with the cue names the
  client builds from the character id in cue sheet `InitialVoice`: `Title_{character}_App_ON_01` (`TitleCall`),
  `Title_{character}_Company_BR_01` (`SplashCompanyBR`), `Title_{character}_Company_FT_01` (`SplashCompanyFT`),
  `Title_{character}_Company_bili_01` (`SplashCompanyBilibili`).
- `Sound` rows have no characters, category or text in the master data. What their names suggest is in `inferred`,
  apart from the master facts: `situation`, the first of these patterns the cue name matches in full:

  | Situation | Cue |
  |---|---|
  | `SpotTap` | `spot_<n>_<n>_<n>_<name>_<n>[_<n>]_tap[_<n>]` |
  | `SpotAfter` | `spot_<n>_<n>_<n>_<name>_<n>[_<n>]_after[_<n>]` |
  | `SpotIntro` | `spot_<n>_<n>_<n>_<name>_<n>[_nasi]` |
  | `ResultPairTalk` | `Result_<A>_Talk_CP_<B>_Call_<n>`, `..._Res_<n>` |
  | `ResultTalk` | `Result_<A>_Talk_Common_<n>` |
  | `TitleCall` | `Title_<A>_App_<n>` |
  | `SplashCompany` | `Title_<A>_Company_<X>_<n>` |
  | `Location` | `vox_pt<n>_<a>[_<b>]` |

  `characters`, the characters whose name token (the last `_` part of `_nameTextID`) is a word of the cue name, in
  the order they appear; `rows`, the `HomeSpot` rows whose voice is in the same sheet.

### Story episodes

`Story` reads every row of `MasterAdv` (the `story` rules). The row's `_advEpisodeAsset` names the episode: catalog
key `Adv/Episode/<asset>/<asset>`, an AdvEpisodeCollection MonoBehaviour whose `Collection` is the command list,
and three shards at the same key with a suffix, TextAssets of JSON rows: `-Text` (texts in the five languages, the
columns of MasterText), `-Sound` (sounds, the columns of MasterSound) and `-SoundCueSheet` (cue sheets, the columns
of MasterSoundCueSheet). A command with `VoiceIDs` gives one row per voice id:

- `source`: `row` the adv id, `command` the command's `Index`, `type` its AdvCommand value and name (`Talk`,
  `ChatTalk`, `Voice`, ...), `slot` (`1`, `2`, ...) when the command has several voice ids, `ignoreData` when its
  `IgnoreData` is set.
- `text`: `AdvTextID` with its row of the episode's `-Text` (`texts` null when that shard has no such row or is
  not read); `speaker`: `TargetName` and each of `TargetTextIDs` with its `-Text` row.
- `sound`: the voice id with its `-Sound` row's `_category` and `_cueName`, and the `_cueSheetName` of the
  `-SoundCueSheet` row of its `_soundCueSheetID`.
- `category`: the episode's kind; `characters` is empty (the episode data names speakers, not characters).
- `inferred`, apart from these facts: rule `speaker-name`, the segments of `TargetName` split at `・` that are,
  case ignored, a name part of one character: a `_` part of its `_nameTextID` after the leading parts every
  character's `_nameTextID` shares (its name token, the last part, or another name the id gives it). `characters`
  in speaker order and `matches` (`{segment, character}`); a part two characters have matches neither. A speaker
  without such a segment has no `inferred`.

The kind is that of the first table below (then by row id) with a row whose `_advId` is the episode; its fields and
names are listed on the episode. An episode none of them plays has kind null.

| Kind | Table | Fields | Names |
|---|---|---|---|
| `Main` | `MasterStoryEpisode` | `chapter` `_chapterId`, `episode` `_episodeNumber`, `character` `_characterId`, `another` `_isAnotherEpisode`, `extra` `_isExtraEpisode` | `chapter`: `MasterStoryChapter` `_nameTextId` of `_chapterId` |
| `Friendship` | `MasterStoryFriendshipEpisode` | `friendship` `_characterFriendshipId`, `episode` `_episodeNumber`, `characterA` / `characterB`: `MasterCharacterFriendship` `_masterCharacterIdA` / `_masterCharacterIdB` | |
| `HomeSpotTapTalk` | `MasterStoryHomeSpotTapTalkEpisode` | `spot` `_spotId`, `character` `_characterId` | |
| `LiveResult` | `MasterStoryLiveResultEpisode` | `characters` `_characterIds` | |
| `HomeSpot` | `MasterHomeSpot` | `spot` `_id` | `spot` `_nameTextId` |

The episodes are the document's `episodes`, by adv id: `id`, `asset`, `title` (`_titleTextId`), `kind`, `table`,
`row`, `fields`, `names`, `status`, `detail` and `voices` (its rows). An episode's status:

| Status | Meaning |
|---|---|
| `no-value` | the MasterAdv row has no asset |
| `missing-key` | the catalog has no key for the episode (a gap) |
| `not-exported` | the key exists; no `unity.export` result holds its MonoBehaviour |
| `unsupported` | the MonoBehaviour has no `Collection`, or a shard is not of the form above (`detail`) |
| `ok` | its voices are rows |

A voice of a home or after-live episode is often a cue that a row of another source also names; both rows are kept.

### Cues and streams

The sound's `_soundCueSheetID` names the cue sheet (MasterSoundCueSheet `_cueSheetName`, catalog key
`Cri/Sound/<sheet>`) and `_cueName` the cue. The cue's streams come from the sheet's ACB tables (`nnnotes.acb`): a
cue references a waveform, a synth, a sequence or a block sequence; synths reference waveforms, synths and
sequences; the tracks of sequences and blocks note on synths and sequences. A waveform of the memory AWB is the
stream at its position in the AWB, the numbering of the sheet's `streams.json`. A cue may play several streams (a
random or layered cue) and several cues one stream. The same cue name in two sheets is two cues. The waveform's
sample count, rate and channels are compared with the decoded stream; a difference is noted in `detail`.

### Statuses

`status` is the first of these that holds; `availability` keeps the facts apart: `master` (the master data
resolves the sound to a sheet and a cue), `catalog` (the sheet's key is a catalog key; `null` when not looked up)
and `exported` (the cue's streams are decoded).

| Status | Meaning |
|---|---|
| `no-value` | no MasterSound row for the sound, or no MasterSoundCueSheet row for its sheet (`detail`); for `Story`, no `-Sound` row for the voice id or no `-SoundCueSheet` row for its sheet |
| `missing-key` | the catalog has no key `Cri/Sound/<sheet>`; for `Story` also: no key for the episode's `-Sound` or `-SoundCueSheet` (`detail`) |
| `not-exported` | the key exists; the export has no `cri.audio` result for its content (for `Story` also: the `-Sound` or `-SoundCueSheet` shard is not exported) |
| `unsupported` | the sheet's `cri.audio` task did not decode it (`detail`: the reason code) |
| `missing-cue` | the sheet is decoded; its ACB has no such cue, or the cue plays none of its streams |
| `ok` | the cue's streams are in `audio` |

Gaps are the rows that are `missing-key` or `missing-cue`, whatever their source, and the `missing-key` episodes.
Whether a site shows a voice is not recorded: sites are built separately.

### Documents

`nnnotes.voices/1`, canonical JSON:

```json
{"schema": "nnnotes.voices/1", "view": "voices", "version": 2, "rules": "<digest>", "tables": ["MasterAdv", "..."],
 "characters": [{"id": 1, "token": "Alpha", "names": {"name": {"ja": "...", "en": "..."}, "short": {}, "en": {}}}],
 "rows": [{"id": "MasterTalk:561",
           "source": {"name": "Talk", "table": "MasterTalk", "row": 561, "column": "_voiceSoundId",
                      "type": {"enum": "CharacterVoiceMasterType", "value": 0, "name": "Talk"}},
           "characters": [1], "category": {"name": "CharacterRankUp", "enum": "TalkCategory", "value": 1},
           "sound": {"id": 100000001, "category": 2, "sheet": "VoiceSystem_01", "cue": "Growth_Alpha_RankUp_01"},
           "text": {"id": "Talk_Text_561", "texts": {"ja": "...", "en": "...", "zh-Hant": "...", "zh-Hans": "...", "ko": "..."}},
           "conditions": {"_costumeId": 1, "_unlockCharacterRank": 1},
           "availability": {"master": true, "catalog": true, "exported": true}, "status": "ok",
           "audio": {"cueId": 1, "lengthMs": 1000,
                     "streams": [{"stream": 0, "file": "Growth_Alpha_RankUp_01.flac",
                                  "artifact": "cri.audio:<sha256>#Growth_Alpha_RankUp_01.flac",
                                  "sha256": "...", "size": 1, "sampleRate": 48000, "channels": 1,
                                  "samples": 48000, "seconds": 1.0}]}},
          {"id": "MasterAdv:900001:3",
           "source": {"name": "Story", "table": "MasterAdv", "row": 900001, "command": 3,
                      "type": {"enum": "AdvCommand", "value": 2, "name": "Talk"}},
           "characters": [], "category": {"name": "Main"},
           "sound": {"id": 5001, "category": 2, "sheet": "adv_voice_x_01", "cue": "adv_voice_x_01_001"},
           "text": {"id": "x01_1", "texts": {"ja": "...", "en": "..."}},
           "speaker": {"name": "alpha", "texts": [{"id": "adv_alpha", "texts": {"ja": "...", "en": "..."}}]},
           "inferred": {"characters": [1], "rule": "speaker-name", "matches": [{"segment": "alpha", "character": 1}]},
           "availability": {"master": true, "catalog": true, "exported": true}, "status": "ok", "audio": {}}],
 "episodes": [{"id": 900001, "asset": "adv_script_x_01", "title": {"id": "...", "texts": {}}, "kind": "Main",
               "table": "MasterStoryEpisode", "row": 101, "fields": {"chapter": 1, "episode": 1},
               "names": {"chapter": {"ja": "..."}}, "status": "ok", "voices": 1}],
 "snapshot": {"tables": {"MasterTalk": "<sha256>"}, "sheets": {"VoiceSystem_01": {"task": "cri.audio:<sha256>",
                                                                                 "acb": "<sha256>", "status": "exported"}},
              "episodes": "<sha256>"},
 "coverage": {"rows": 1, "sources": {"Talk": {"rows": 1, "empty": 0, "counts": {"ok": 1}, "gaps": []},
                                     "Story": {"rows": 1, "empty": 0, "counts": {"ok": 1}, "gaps": [],
                                               "episodes": {"count": 1, "counts": {"ok": 1}, "gaps": []}}},
              "reverse": {"prefixes": ["Cri/Sound/Voice"], "keys": 1, "unreferencedKeys": [], "streams": 1,
                          "unreferencedStreams": {}}}}
```

- Row ids: `<Table>:<row id>` (`:<slot>` for a row with two voices), `Title:<character>:<category>`,
  `MasterSound:<id>`, `MasterAdv:<adv id>:<command index>` (`:<slot>` for a command with several voice ids). Rows
  are in the order of the sources, then by id (`Story`: by adv id, then in the episode's command order); a row of
  `Title` has `source.rows` and `source.cue` (the template) and no sound id; a `HomeSpot` row has `names.spot`, a
  `MemberCard` row `names.card`.
- `snapshot`: the content id of every master table read, per decoded sheet its `cri.audio` task and ACB, and the
  content id of the `episodes` input (below).
- Coverage. Forward, per source: rows, rows per status, `empty` and `gaps`; for `Story` also `episodes` (their
  number, number per status and the `missing-key` ones), `empty` counting the `ok` episodes without voices.
  Reverse: the catalog keys under the
  prefixes (`Cri/Sound/Voice`, `Cri/Sound/spot_`, `Cri/Sound/InitialVoice`, `Cri/Sound/adv_voice_`), the ones whose
  sheet no row names (`unreferencedKeys`), and per decoded sheet the streams no row reaches
  (`unreferencedStreams`: `[stream, name]`).

### Stage

- Subjects: the catalog subjects of `link.addresses`, when master data is set and `voices` is selected.
- Inputs: `master:<Table>` for every table the rules read, `rules` (the format and the `voices` rules), `addresses`
  (the keys it looks up and the prefixes it lists, as `views.Recorder` records them), `sheets` (each sheet the
  `cri.audio` results are known by, as `cristages.AudioStage` names them, that a row of the master sources names or
  the prefixes list: its task and ACB content id), `acb:<sha256>` for the ACB of every decoded one, `episodes` (per
  episode asset and part: the object its key names, the MonoBehaviour of the script or the TextAsset of a shard; the
  `unity.export` artifact that holds it, `json` or `data`, found through the address table's serialized files; its
  content id) and `adv:<sha256>` for each of those contents; context `audio`, the keys of the `cri.audio` results.
  It runs after `unity.export` and `cri.audio` and waits for their tasks; without them every present key and
  episode is `not-exported`. A story voice is looked up in the sheets it reads.
- Artifact `view.voices:<subject>#view`; facts `rows`, `entries` (rows per status), `gaps` (`export --strict`
  exits 1 when there are any) and `unreferenced` (the streams of the decoded sheets that no row reaches).
- Derived document of the `original` layout: `views/voices.json`, with `"layout": "original"` and the `path` of
  every stream (`null` when the layout places none).
