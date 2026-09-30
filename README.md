# nnnotes？！

[简体中文](README.md) | [English](README.en.md)

nnnotes 是 BanG Dream! Our Notes 游戏文件的离线数据工具包：读取 Addressables catalog、下载并解密资源包、下载与解码 masterdata，再把剧情、Live2D、据点场景、着色器、CRI 音频和谱面导出成结构化的 JSON 与通用格式文件。`web` 导出的谱面、Live2D 模型与剧情站点就是 [ournotes-player](https://github.com/empty-sekai/ournotes-player) 读取的数据。命名灵感来自 [mos9527/sssekai](https://github.com/mos9527/sssekai)。

本项目为非官方爱好者项目，与游戏的开发和运营方无关。仓库不包含任何游戏资源、密钥或服务器地址：游戏文件、解密所需的密钥和服务器地址都由使用者在配置中自行提供，导出结果只保存在使用者指定的本地目录。

## 安装与初始化

```sh
pip install nnnotes              # Python 3.11+，推荐 3.13
nnnotes config init --user       # 在终端里逐项填写设置（密钥不回显），写入用户配置文件，在任何目录下都会读取
nnnotes config check             # 每项设置的来源和格式是否有效，不显示设置值
```

- 设置值（密钥、区服的 CDN 与 API 地址、本地数据与工具的路径）都来自使用者自己的游戏客户端，见[配置](#配置)。
- 在脚本或 AI 代理中不会有交互提问：`nnnotes config init --user --no-input --set 段.键=值 ...` 一次写好，之后用 `nnnotes config set 段.键 值` 修改单项（值写 `-` 时从标准输入读取，密钥不必出现在命令行里），`nnnotes config check --json` 给出机器可读的状态。
- `--fonts game` 另需 `pip install 'nnnotes[fonts]'`；APK、masterdata 与外部工具见[需要准备](#需要准备)。

## 功能

| 命令 | 输入 | 输出 |
|---|---|---|
| `catalog` | 区服 catalog | 列出 Addressables 资源键（可按前缀过滤） |
| `browse` | 已配置的各区服 catalog | 本地网页浏览 catalog 与资源包（`--host` / `--port`） |
| `pull` | 资源键 | 下载并解密该键依赖闭包中的全部资源包到本地缓存 |
| `servers` | 引导 API 根地址 `[bootstrap] api` | 游戏 API 的服务器列表：各区服名称、区域 ID 与 CDN / API 根地址（地址仅在 `--show-hosts` 时输出） |
| `master version` | 区服 | 该区服当前的 masterdata 版本与资源版本（游戏 API 匿名调用） |
| `master download` | masterdata 版本号，或 `--latest`（区服当前版本） | 该版本的 `MasterManifest.json` 与全部 `.bin` 文件（SHA-256 校验） |
| `master decode` | masterdata `.bin` 文件或目录 | 每张表一个 JSON（Rijndael-256 CBC 解密 + gzip 解压） |
| `adv` | 剧情 ID | `episode.json`：命令表、五语台词、语音 / 音效 / 视频索引 |
| `story` | 剧情 ID | 剧情目录：episode、音频、舞台场景与着色器、剧情 UI、Frame / 粒子特效 / 后处理 / 静帧 / 对话框 / 聊天资源、视频（WebM）；用到的 Live2D 模型导出到模型目录（`--models`，默认为剧情目录旁的 `live2d/`），已导出的模型供其他剧情复用 |
| `live2d` | 模型资源键或模型 ID | Live2D（Cubism）运行时目录：moc3、贴图、motion3、物理、表情、预制体参数 |
| `spot` | 据点 ID | `spot.json` + Spine 角色 + 房间 `room.glb` + 着色器 |
| `room` | 背景预制体键 | 房间模型（binary glTF） |
| `shader` | 资源键或 APK 内资源包 | 着色器各平台变体（GLSL ES 等）与索引 |
| `audio` | CRI cue sheet | 每个 cue 一个音频文件（FLAC / Ogg / WAV）+ cue 元数据 |
| `crikey` | APK | 读出游戏启动数据中的 CRI HCA 解码密钥（只显示是否找到，可写成 `.hcakey`） |
| `player` | APK | 渲染相关的全局设置（色彩空间、画质等级、渲染器）JSON |
| `ui`（实验性） | APK 集合及内嵌 catalog | 离线序列化预制体库，包含依赖根对象、AnimatorController、稳定引用与 PNG／字体资源，供 ournotes-player 可选 UI 预览使用（[格式与边界](https://github.com/MetaSekaiLab/nnnotes/blob/main/docs/ui.md)） |
| `live` | 曲目 ID + 难度 | 完整谱面目录：谱面与运行时音符、3D 场景、音符与特效资源、BGM 与音效、声音路由 |
| `web` | `--pair 曲目:难度`（可重复）或 `--all`；`--live2d 模型`（可重复）或 `--all-live2d`；`--story 剧情 ID`（可重复）或 `--all-stories`；`--region 区服`（可重复）或 `--all-regions` | ournotes-player 静态站点：共享播放器 + 每谱 / 每模型 / 每集剧情清单 + 内容寻址资源；剧情用到的 Live2D 模型先按模型构建并列入 `models.json`，剧情清单引用它们；一个站点可服务多个区服，列表文本含五种语言；剧情的界面文字按语言分组，字形由开源字体生成 TextMeshPro 字体资源（`--fonts game` 时用游戏字体）；可压缩的资源（JSON、着色器、moc3 等）默认以 gzip 存储，`--compress br` / `none` 可改 |
| `music-data` | masterdata 文件（`--master-files` 目录或 `--apk-master`），或带清单的解码 masterdata（`--decoded-master`） | 全部歌曲与谱面的单个 JSON：五语标题与作词作曲编曲、乐队、演唱角色、分类、标签、上线时间、评级线、BGM 时长；激走目录（成员卡、小卡及其激走技能与激走支援技能）；每个难度的等级、音符数、BPM、谱面时间、技能事件与 fever 区间；以及组卡模型 ournotes-deck（内置于 nnnotes）在整场模拟上实测的谱面统计（无技能得分、每种加分技能在每个演出位的权重），以及单个激走技能形状的谱面适性（增量与标准误，不选最佳编成；`--no-gekisou-aptitude` 可跳过）。`--full` 另附组卡模型的输入：每张谱面的运行时音符与卡牌、技能、加成、分数、活动相关的 masterdata 表（[格式](docs/music-data.md)） |

导出约定：

- 写文件的命令都用 `-o` 指定输出路径（必填）；`web` 的站点目录是位置参数。
- JSON 一律 UTF-8、LF 换行；无穷大写作 `1e999`。
- 在同一套环境下导出是确定的：同样的输入，在 nnnotes、Python 依赖库（UnityPy、Pillow、numpy 等）和外部工具（vgmstream、FFmpeg）版本都相同时，导出文件逐字节相同。版本不同时，相同内容可能写成不同的字节：例如不同版本的 Pillow 写出的 PNG 像素相同，字节可能不同。
- 数值保留 Unity 序列化值与字段名（`m_LocalPosition`、`_bandIDs` 等），便于与游戏数据对照。
- 贴图导出为 PNG，着色器保留游戏自带的编译结果，音频由 CRI 格式解码为通用格式。

## 完成度

日服现已支持匿名版本查询、CDN 认证下载、gzip catalog、资源路径与缓存隔离，以及 Android 分包读取。
配置、命令和日服验证范围见 [日服支持](docs/jp.md)。

以下结果基于台服 1.0.1（zh-Hant）的全部数据：

| 部分 | 状态 |
|---|---|
| catalog / 资源包解密 / 依赖闭包 | 可用 |
| masterdata 解码 | 可用 |
| 剧情 `adv` | 946 / 946 集可导出 |
| 剧情 `story`（完整目录） | 946 / 946 集的资源都在 catalog 中且类型均受支持（资源闭包与游戏自带的每集下载清单一致）；946 / 946 集已逐集导出验证：输出中引用的文件、模型与音频均存在，JSON 均可解析，引用的路径均可解析；其中 235 集用到 Frame / Effect / PostEffect / Still / Chat / TalkWindow / 视频资源，两次导出除模型目录路径外逐字节一致 |
| Live2D 模型 | 239 / 239 个可导出（catalog 中的全部模型，剧情用到其中 185 个） |
| CRI 音频 | 681 / 681 个 cue sheet 可解码 |
| 谱面 `live` | 336 / 336 个（曲目, 难度）组合可导出 |
| 网页站点 `web` | 336 / 336 张谱面，239 / 239 个 Live2D 模型，946 / 946 集剧情（英语、无音频的全量构建两次结果逐字节一致，946 / 946 集通过数据校验）；三个区服、五种语言、含 AAC 音频的全量构建（`--all-regions`，用线上 masterdata：85 首曲目的 340 张谱面）两次结果同样逐字节一致，全部通过数据校验 |
| 据点 `spot` / `room` | 39 / 39 个据点可导出，引用均可解析；catalog 中 20 / 20 个房间背景可由 `room` 导出。生成的 glb 均通过 Khronos glTF-Validator，0 个错误 |
| 其他区服（en / kr）与其他语言 | 已核对：各区服同一语言的 catalog 相同、资源包相同，密钥通用；谱面用到的 masterdata 表在三个区服间一致，文本表五种语言齐全；抽查的谱面导出与台服一致。多区服整站已全量构建验证（见上一行） |

## 需要准备

- Python 3.11+，推荐 3.13（`web` 构建读写大量 JSON，3.13 的标准库 JSON 编码更快）；开发时在仓库内 `pip install -e .`
- 游戏安装包 `base.apk`：APK 内置资源包、CRI 解码密钥、启动设置。`player`、`story`、`live`、`web` 用 nnnotes 自带的类型树读取 APK 启动数据中的 MonoBehaviour，目前支持游戏版本 1.0.1（Unity 6000.3.12f1）；其他版本的 APK 若类型不符，这些命令会报错并给出类名、游戏版本和 Unity 版本
- 解码后的 masterdata 目录（可用 `master download` + `master decode` 生成）：`adv`、`story`、`spot`、`live` 和 `web` 的谱面需要；`web` 的 Live2D 模型只用它取角色名（可选）
- 外部工具：[vgmstream](https://vgmstream.org/)（CRI HCA 解码）、[FFmpeg](https://ffmpeg.org/)（转码，含剧情视频的 WebM 封装与 Opus 音频）；`web` 另需 Node.js 20+ 与构建好的 ournotes-player

## 配置

代码中没有任何密钥、服务器地址和默认路径。所有设置按以下顺序读取，后者覆盖前者：

1. 配置文件：`--config <文件>`，否则 `NNNOTES_CONFIG`，否则当前目录的 `nnnotes.toml`，否则用户配置文件（Windows 为 `%APPDATA%\nnnotes\nnnotes.toml`，其他系统为 `~/.config/nnnotes/nnnotes.toml`；`nnnotes config path` 列出查找顺序与实际读取的文件）
2. 环境变量：`NNNOTES_<节>_<键>`（如 `NNNOTES_BUNDLE_KEY`、`NNNOTES_SERVERS_TW_CDN`）
3. 命令行参数：`--region`、`--language`、`--catalog`、`--cache`、`--master`、`--apk`、`--ffmpeg`、`--vgmstream`、`--node` 写在命令名之前；`--player` 是 `web` 的参数

`nnnotes config init` 写出配置模板（每项设置为空，模板随包安装，仓库中为 [`src/nnnotes/nnnotes.example.toml`](src/nnnotes/nnnotes.example.toml)），在终端中会逐项询问；`config set` / `config unset` 修改单项并保留文件中的注释。需要的设置包括：资源包解密密钥与 nonce 种子、masterdata 的密钥与 IV、使用的区服 `[catalog] region` 与 catalog 语言 `[catalog] language`、每个区服的 CDN 地址与 API 根地址（`[servers.<区服>]` 的 `cdn`、`api`）、客户端版本 `[client] version`（未设置时读取 APK 的 versionName）、可选的引导 API 根地址 `[bootstrap] api`（`servers` 使用），以及缓存目录、APK、masterdata 目录、ournotes-player 和 vgmstream / FFmpeg / Node.js 的路径（三个工具未设置时在 `PATH` 中查找）。这些值都来自使用者自己的游戏客户端。

缺少或格式错误的设置会让命令以退出码 2 结束，并用一行说明对应的配置键、环境变量和命令行参数，不会输出任何设置值。`nnnotes.toml` 已在 `.gitignore` 中，请勿提交。

完整说明见 [docs/configuration.md](docs/configuration.md)。

## 使用示例

```bash
nnnotes catalog --prefix Live/MusicScore/ --limit 20
nnnotes browse --port 8000
nnnotes pull Live/MusicScore/0001/0001_03
nnnotes master version
nnnotes master download --latest -o work/master-bin
nnnotes master decode work/master-bin -o work/master
nnnotes --master work/master adv 10462 -o out/adv_10462.json
nnnotes story 10462 -o out/story_10462
nnnotes live 100001 --difficulty expert -o out/live_100001
nnnotes web out/site --all --player <ournotes-player 目录> --workers 5
nnnotes web out/site --pair 100001:expert --pair 100001:hard --player <ournotes-player 目录>
nnnotes web out/site --all --region tw --region en --region kr --player <ournotes-player 目录>
nnnotes web out/site --live2d adv_live2d_rana_003_casual_spring_01 --player <ournotes-player 目录>
```

`web` 可以增量构建：清单已存在的谱面和模型会跳过（`--force` 重建），不再被引用的资源会被清理。常用参数：

- `--format aac|opus|vorbis|mp3|flac`：BGM 格式，默认 AAC；`--no-audio`：不导出音频
- `--band` / `--leader-card`：轻量背景与开场时间轴所用乐队；默认取曲目第一位演唱角色的乐队
- `--workers`：并行处理的曲目进程数（默认为 CPU 数的四分之一，最多 8）与模型进程数（默认最多 4）；`--read-workers`：同时运行的谱面读取集（Node.js 进程）数（默认为 CPU 数的一半，最多 16）；`--tmp`：临时构建目录（默认 `<站点>.tmp`）
- `--live2d 模型`（模型 ID 或资源键，可重复）/ `--all-live2d`：加入 Live2D 模型（catalog 中全部模型），可与谱面在同一次构建中加入
- `--region 区服`（可重复）/ `--all-regions`：站点服务的区服（默认 `[catalog] region`）；每个区服的 masterdata 由 `[servers.<区服>] master` 指定。谱面数据相同的区服共用一份清单，列表页用 `?region=&lang=` 切换区服与语言
- `--player-only`：只重写播放器文件与 `charts.json`、`models.json`；`--reingest-json`：按当前规则重新存储所有谱面与模型的 JSON

各命令的参数与输出目录结构见 [docs/commands.md](docs/commands.md)。

## 架构

```
配置（TOML / 环境变量 / 参数）
  └─ 访问层      addressables（catalog 解析、资源包解密、本地浏览）
                 catalog（依赖闭包、远端与 APK 内资源、本地缓存）
                 master（masterdata 下载与解码）
                 gameapi（游戏 API 匿名调用：当前 masterdata 版本、服务器列表）
       └─ 读取层  unity（UnityPy 读取 typetree 与 TextAsset）
                  export（预制体 / 组件 / 引用全部解析为 JSON，贴图与着色器随同导出）
                  shader、textstyle（文字排版与样式）、tmpfont（TextMesh Pro 字体）、player（启动设置）、cri + crikey（CRI 音频）
            └─ 内容层  剧情：adv、advscene、advmedia（Frame / 特效 / 后处理 / 静帧 / 聊天）、advvideo（USM 视频）、advui、story
                       Live2D：live2d、motion
                       据点：spot、room
                       谱面：score（谱面解析与游戏谱面转换器的复现）、livescene、livenotes、liveui、liveaudio、live
                       站点：web、webmodel（ournotes-player 数据）
```

所有 JSON 由同一个写出器生成（`jsonio`），保证编码、换行与数值格式一致。

`web` 构建的耗时分布，以及其中哪些部分已由编译代码执行，见 [docs/performance.md](docs/performance.md)。

## License

MIT，见 [LICENSE](LICENSE)。
