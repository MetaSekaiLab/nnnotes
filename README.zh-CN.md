# nnnotes

[English](README.md) | [简体中文](README.zh-CN.md)

nnnotes 将你自己的 BanG Dream! Our Notes 游戏文件导出为结构化数据、媒体文件和静态播放器站点。
它读取 Addressables 目录、资源包、主数据和 APK 资源，导出剧情、Live2D、谱面、房间、UI、着色器与音频，
并为 [ournotes-player](https://github.com/empty-sekai/ournotes-player) 组装站点。

持久化的 **depot** 保存转换后的对象、任务结果和目录版本。各次导出共享这些结果；命名站点在构建之间保留
所选内容。depot 也可以通过共享目录或兼容 S3 的对象存储在机器之间复用工作。

**[文档](https://metasekailab.github.io/nnnotes/)** ·
[快速开始](https://metasekailab.github.io/nnnotes/quickstart/) ·
[命令参考](https://metasekailab.github.io/nnnotes/cli/)

## 安装

需要 Python 3.11 或更新版本：

```sh
pip install nnnotes==0.3.0
pip install 'nnnotes[fonts]==0.3.0'    # 剧情与 UI 字体
pip install 'nnnotes[remote]==0.3.0'   # 兼容 S3 的 depot 远端
pip install 'nnnotes[deck]==0.3.0'     # music-data 谱面统计
```

按需选择附加依赖，也可以组合为 `nnnotes[fonts,remote,deck]==0.3.0`。
音视频转换需要 [vgmstream](https://vgmstream.org/) 与 [FFmpeg](https://ffmpeg.org/)。
站点构建另需 Node.js 22+ 和已构建的 **ournotes-player 0.2.x** 包。
`deck` 附加依赖安装 `ournotes-sim==0.0.12`。

## 第一次导出

通过配置向导提供自己的客户端文件、密钥与服务地址：

```sh
nnnotes config init --user
nnnotes config check
nnnotes master download --latest -o work/master-bin
nnnotes master decode work/master-bin -o work/master
nnnotes config set paths.master work/master
nnnotes export episode 10462 -o out/episode.json
nnnotes export story 10462 --fonts game -o out/story
```

将 `paths.depot` 设为持久目录，`paths.apk` 设为游戏 APK。向导输入密钥时不回显；`config check` 显示配置项名称、
来源及有效性。剧情导出需要 `fonts` 附加依赖及音频工具。完整前置条件和输出结构见
[快速开始](https://metasekailab.github.io/nnnotes/quickstart/)。

## 构建播放器站点

```sh
npm install ournotes-player@0.2.0
nnnotes site build --name stories -o out/site --story 10462 --fonts game --player node_modules/ournotes-player
node node_modules/ournotes-player/scripts/validate-data.mjs out/site
python -m http.server 8000 --directory out/site
```

打开 `http://localhost:8000/story/`。Live2D、MotionSync 和 Spine 功能需要嵌入页面提供对应的浏览器运行时，
具体见[站点指南](https://metasekailab.github.io/nnnotes/site/)。
用 `--pair MUSIC_ID:DIFFICULTY` 选择谱面，`--live2d KEY` 选择模型，`--story-languages ja,en` 选择剧情语言。
复用同一站点名会更新本次选中的内容，并保留其他内容。

## 选择工作流

| 任务 | 命令 | 指南 |
|---|---|---|
| 配置输入 | `config init`、`config check`、`servers` | [配置](https://metasekailab.github.io/nnnotes/configuration/) |
| 查看与保存目录 | `catalog keys`、`browse`、`versions`、`import`、`fetch`、`diff` | [命令](https://metasekailab.github.io/nnnotes/cli/) |
| 复制源资源包 | `bundle pull` | [资源导出](https://metasekailab.github.io/nnnotes/assets/) |
| 导出主题 | `export episode`、`story`、`live`、`live2d`、`spot`、`room`、`shader`、`audio`、`ui` | [导出](https://metasekailab.github.io/nnnotes/export/) |
| 转换指定资源 | `export assets`、`depot plan`、`depot run` | [资源导出](https://metasekailab.github.io/nnnotes/assets/) |
| 检查与保留存储结果 | `depot verify`、`depot gc` | [Depot](https://metasekailab.github.io/nnnotes/depot/) |
| 导出歌曲与谱面统计 | `music-data` | [音乐数据](https://metasekailab.github.io/nnnotes/music-data/) |
| 查找角色语音 | `voices list`、`search`、`summary`、`get` | [命令](https://metasekailab.github.io/nnnotes/cli/) |

命令默认打印摘要；`--json` 输出结构化报告。退出状态和各项参数见命令参考。

## 数据与兼容性

产物通过版本化清单和共享对象记录描述。消费这些数据时，请查阅
[格式](https://metasekailab.github.io/nnnotes/formats/)、[产物](https://metasekailab.github.io/nnnotes/products/)和
[Schema](https://metasekailab.github.io/nnnotes/schemas/)参考。
任务标识包括输入内容、选项和转换实现，其中也包括原生编码器。
[确定性说明](https://metasekailab.github.io/nnnotes/determinism/)解释不同安装环境中的可复现性。

版本覆盖 CLI、配置、Schema 和文档中的 Python API。1.0 之前，次版本可以改变这些接口。
改动见 [CHANGELOG.md](CHANGELOG.md)，实现分层见[架构](https://metasekailab.github.io/nnnotes/architecture/)。

## 贡献与许可

[CONTRIBUTING.md](CONTRIBUTING.md) 说明开发和合成测试流程。nnnotes 采用 [MIT](LICENSE) 许可。
这是非官方爱好者项目，与游戏开发或运营方无关。游戏资源、密钥和服务配置由各用户自行提供，并受其权利人的权利约束。
