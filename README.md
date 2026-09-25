# nnnotes

[简体中文](README.md) | [English](README.en.md)

nnnotes 是 BanG Dream! Our Notes 游戏文件的离线数据工具包：读取 Addressables catalog、下载并解密资源包、下载与解码 masterdata，再把剧情、Live2D、据点场景、着色器、CRI 音频和谱面导出成结构化的 JSON 与通用格式文件。谱面导出结果就是 [ournotes-player](https://github.com/empty-sekai/ournotes-player) 读取的数据。

本项目为非官方爱好者项目，与游戏的开发和运营方无关。仓库不包含任何游戏资源、密钥或服务器地址：游戏文件、解密所需的密钥和服务器地址都由使用者在配置中自行提供，导出结果只保存在使用者指定的本地目录。

## 功能

| 命令 | 输入 | 输出 |
|---|---|---|
| `catalog` | 区服 catalog | 列出 Addressables 资源键（可按前缀过滤） |
| `browse` | 已配置的各区服 catalog | 本地网页浏览 catalog 与资源包（`--host` / `--port`） |
| `pull` | 资源键 | 下载并解密该键依赖闭包中的全部资源包到本地缓存 |
| `master download` | masterdata 版本号 | 该版本的 `MasterManifest.json` 与全部 `.bin` 文件（SHA-256 校验） |
| `master decode` | masterdata `.bin` 文件或目录 | 每张表一个 JSON（Rijndael-256 CBC 解密 + gzip 解压） |
| `adv` | 剧情 ID | `episode.json`：命令表、五语台词、语音 / 音效 / 视频索引 |
| `story` | 剧情 ID | 完整剧情目录：episode、全部 Live2D 模型、音频、舞台场景与着色器、剧情 UI |
| `live2d` | 模型资源键 | Live2D（Cubism）运行时目录：moc3、贴图、motion3、物理、表情、预制体参数 |
| `spot` | 据点 ID | `spot.json` + Spine 角色 + 房间 `room.glb` + 着色器 |
| `room` | 背景预制体键 | 房间模型（binary glTF） |
| `shader` | 资源键或 APK 内资源包 | 着色器各平台变体（GLSL ES 等）与索引 |
| `audio` | CRI cue sheet | 每个 cue 一个音频文件（FLAC / Ogg / WAV）+ cue 元数据 |
| `crikey` | APK | 读出游戏启动数据中的 CRI HCA 解码密钥（只显示是否找到，可写成 `.hcakey`） |
| `player` | APK + IL2CPP 符号 | 渲染相关的全局设置（色彩空间、画质等级、渲染器）JSON |
| `live` | 曲目 ID + 难度 | 完整谱面目录：谱面与运行时音符、3D 场景、音符与特效资源、BGM 与音效、声音路由 |
| `web` | `--pair 曲目:难度`（可重复）或 `--all` | ournotes-player 静态站点：共享播放器 + 每谱清单 + 内容寻址资源 |

导出约定：

- 写文件的命令都用 `-o` 指定输出路径（必填）；`web` 的站点目录是位置参数。
- JSON 一律 UTF-8、LF、确定性输出；同样的输入两次导出逐字节相同。无穷大写作 `1e999`。
- 数值保留 Unity 序列化值与字段名（`m_LocalPosition`、`_bandIDs` 等），便于与游戏数据对照。
- 贴图导出为 PNG，着色器保留游戏自带的编译结果，音频由 CRI 格式解码为通用格式。

## 完成度

以下结果基于台服 1.0.1（zh-Hant）的全部数据：

| 部分 | 状态 |
|---|---|
| catalog / 资源包解密 / 依赖闭包 | 可用 |
| masterdata 解码 | 可用 |
| 剧情 `adv` | 946 / 946 集可导出 |
| 剧情 `story`（完整目录） | 712 / 946 集可导出；其余 234 集用到尚未支持的资源类型（Frame 209 集、Effect 17 集、PostEffect 3 集，另有 5 集引用的资源不在 catalog 中） |
| Live2D 模型 | 185 / 185 个可导出 |
| CRI 音频 | 681 / 681 个 cue sheet 可解码 |
| 谱面 `live` | 336 / 336 个（曲目, 难度）组合可导出 |
| 网页站点 `web` | 336 / 336 张谱面 |
| 据点 `spot` / `room` | 已验证单个据点，其余未逐一验证 |
| 其他区服（en / kr）与其他语言 | 未验证 |

## 需要准备

- Python 3.11+，在仓库内 `pip install -e .`（尚未发布到 PyPI）
- 游戏安装包 `base.apk`：APK 内置资源包、CRI 解码密钥、启动设置
- 该 APK 的 IL2CPP 符号（Il2CppDumper 生成的 DummyDll 目录）：`player`、`story`、`live`、`web` 需要
- 解码后的 masterdata 目录（可用 `master download` + `master decode` 生成）：`adv`、`story`、`spot`、`live`、`web` 需要
- 外部工具：[vgmstream](https://vgmstream.org/)（CRI HCA 解码）、[FFmpeg](https://ffmpeg.org/)（转码）；`web` 另需 Node.js 20+ 与构建好的 ournotes-player

## 配置

代码中没有任何密钥、服务器地址和默认路径。所有设置按以下顺序读取，后者覆盖前者：

1. 配置文件：`--config <文件>`，否则 `NNNOTES_CONFIG`，否则当前目录的 `nnnotes.toml`
2. 环境变量：`NNNOTES_<节>_<键>`（如 `NNNOTES_BUNDLE_KEY`、`NNNOTES_SERVERS_TW_CDN`）
3. 命令行参数：`--region`、`--language`、`--catalog`、`--cache`、`--master`、`--apk`、`--dummy-dll`、`--ffmpeg`、`--vgmstream`、`--node` 写在命令名之前；`--player` 是 `web` 的参数

复制 [`nnnotes.example.toml`](nnnotes.example.toml) 为 `nnnotes.toml` 后填写。需要的设置包括：资源包解密密钥与 nonce 种子、masterdata 的密钥与 IV、使用的区服 `[catalog] region` 与 catalog 语言 `[catalog] language`、每个区服的 CDN 地址（`[servers.<区服>]`），以及缓存目录、APK、IL2CPP 符号、masterdata 目录、ournotes-player 和 vgmstream / FFmpeg / Node.js 的路径（三个工具未设置时在 `PATH` 中查找）。这些值都来自使用者自己的游戏客户端。

缺少或格式错误的设置会让命令以退出码 2 结束，并用一行说明对应的配置键、环境变量和命令行参数，不会输出任何设置值。`nnnotes.toml` 已在 `.gitignore` 中，请勿提交。

完整说明见 [docs/configuration.md](docs/configuration.md)。

## 使用示例

```bash
nnnotes catalog --prefix Live/MusicScore/ --limit 20
nnnotes browse --port 8000
nnnotes pull Live/MusicScore/0001/0001_03
nnnotes master download --version <masterdata 版本> -o work/master-bin
nnnotes master decode work/master-bin -o work/master
nnnotes --master work/master adv 10462 -o out/adv_10462.json
nnnotes story 10462 -o out/story_10462
nnnotes live 100001 --difficulty expert -o out/live_100001
nnnotes web out/site --all --player <ournotes-player 目录> --workers 5
nnnotes web out/site --pair 100001:expert --pair 100001:hard --player <ournotes-player 目录>
```

`web` 可以增量构建：清单已存在的谱面会跳过（`--force` 重建），不再被引用的资源会被清理。常用参数：

- `--format aac|opus|vorbis|mp3|flac`：BGM 格式，默认 AAC；`--no-audio`：不导出音频
- `--band` / `--leader-card`：轻量背景与开场时间轴所用乐队；默认取曲目第一位演唱角色的乐队
- `--workers`：并行处理的曲目进程数（默认最多 5）；`--tmp`：临时构建目录（默认 `<站点>.tmp`）
- `--player-only`：只重写播放器文件与 `charts.json`；`--reingest-json`：按当前规则重新存储所有谱面的 JSON

各命令的参数与输出目录结构见 [docs/commands.md](docs/commands.md)。

## 架构

```
配置（TOML / 环境变量 / 参数）
  └─ 访问层      addressables（catalog 解析、资源包解密、本地浏览）
                 catalog（依赖闭包、远端与 APK 内资源、本地缓存）
                 master（masterdata 下载与解码）
       └─ 读取层  unity（UnityPy 读取 typetree 与 TextAsset）
                  export（预制体 / 组件 / 引用全部解析为 JSON，贴图与着色器随同导出）
                  shader、tmpfont（TextMesh Pro 字体）、player（启动设置）、cri + crikey（CRI 音频）
            └─ 内容层  剧情：adv、advscene、advui、story
                       Live2D：live2d、motion
                       据点：spot、room
                       谱面：score（谱面解析与游戏谱面转换器的复现）、livescene、livenotes、liveui、liveaudio、live
                       站点：site（ournotes-player 数据）
```

所有 JSON 由同一个写出器生成（`jsonio`），保证编码、换行与数值格式一致。

## 参与开发

测试、提交规范与发布流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

MIT，见 [LICENSE](LICENSE)。
