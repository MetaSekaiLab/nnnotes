# nnnotes？！

[English](https://github.com/MetaSekaiLab/nnnotes/blob/main/README.md) | [简体中文](https://github.com/MetaSekaiLab/nnnotes/blob/main/README.zh-CN.md)

nnnotes 是 BanG Dream! Our Notes 游戏文件的离线数据工具包：读取 Addressables catalog，下载并解密资源包，下载并解码
masterdata，再把剧情、Live2D 模型、据点、着色器、CRI 音频和谱面导出成结构化 JSON 与通用格式文件。
`nnnotes site build` 写出 [ournotes-player](https://github.com/empty-sekai/ournotes-player) 播放的静态站点，
`nnnotes music-data` 写出包含全部歌曲与谱面的单个 JSON 文件。命名灵感来自 sssekai。

本项目为非官方爱好者项目，与游戏的开发和运营方无关。仓库不包含任何游戏资源、密钥或服务器地址：游戏文件、密钥和服务器地址
都由使用者在自己的配置中提供，导出结果只保存在使用者指定的本地目录。

**文档（英文）：<https://metasekailab.github.io/nnnotes/>**

## 安装

```sh
pip install nnnotes                  # Python 3.11+
pip install 'nnnotes[fonts]'         # 游戏字体（--fonts game）与 site build 的剧情文字
pip install 'nnnotes[deck]'          # music-data 的组卡模型谱面统计
```

此外还需要游戏的 `base.apk`；读取 masterdata 的命令需要解码后的 masterdata；音频与视频需要外部工具
[vgmstream](https://vgmstream.org/) 和 [FFmpeg](https://ffmpeg.org/)（`site build` 另需 Node.js 20+ 与构建好的
ournotes-player）。

## 快速上手

```sh
nnnotes config init --user          # 逐项询问设置（密钥不回显），写入用户配置文件
nnnotes config check                # 每项设置的来源与是否有效，不显示设置值
nnnotes master download --latest -o work/master-bin
nnnotes master decode work/master-bin -o work/master
nnnotes --master work/master export story 10462 -o out/story_10462
nnnotes site build -o out/site --all --player path/to/ournotes-player
```

每个命令输出简短摘要，加 `--json` 时输出 JSON 报告。脚本中可以不经提问写好配置文件：
`nnnotes config init --user --no-input --set 段.键=值 ...`。

## 命令

| 命令 | 作用 |
| --- | --- |
| `config init\|set\|unset\|check\|path` | 写入、修改和检查配置文件 |
| `servers` | 游戏 API 的服务器列表：各区服及其 CDN 与 API 根地址 |
| `catalog keys\|browse` | 区服 catalog 的资源键；在本地网页中浏览 catalog 与资源包 |
| `catalog versions\|import\|fetch\|diff` | store 中保存的 catalog 版本，以及两个版本之间的变化 |
| `bundle pull` | 下载并解密资源键的依赖闭包到缓存 |
| `master version\|download\|decode` | 区服当前的 masterdata 版本、下载，以及解码为 JSON |
| `export assets` | 经内容寻址的 store，把所选资源包中的每个对象导出为通用格式 |
| `export episode\|story\|live\|live2d\|spot\|room\|shader\|audio\|ui` | 单个对象：ADV 剧情集、完整剧情、谱面、Live2D 模型、据点、房间、着色器、cue sheet、UI 库 |
| `apk settings\|hca-key` | `base.apk` 中的画面设置与 CRI HCA 密钥 |
| `store plan\|run\|verify` | `export assets` 将运行什么及原因、单独运行任务、检查 store |
| `voices list\|search\|summary\|get` | masterdata 中的角色语音与剧情语音 |
| `site build` | ournotes-player 静态站点：谱面、Live2D 模型与剧情，支持多个区服与语言 |
| `music-data` | 全部歌曲与谱面的单个 JSON 文件，附组卡模型的谱面统计 |

[命令参考](https://metasekailab.github.io/nnnotes/cli/)列出了所有参数。

## 文档

- [快速上手](https://metasekailab.github.io/nnnotes/quickstart/)：从空配置到导出一集剧情。
- 操作指南：[配置](https://metasekailab.github.io/nnnotes/configuration/)、
  [导出剧情与谱面](https://metasekailab.github.io/nnnotes/export/)、
  [构建 ournotes-player 站点](https://metasekailab.github.io/nnnotes/site/)、
  [导出全部资源](https://metasekailab.github.io/nnnotes/assets/)、
  [日服](https://metasekailab.github.io/nnnotes/jp/)、
  [music-data](https://metasekailab.github.io/nnnotes/music-data/)。
- 参考：[命令](https://metasekailab.github.io/nnnotes/cli/)、
  [命令输出](https://metasekailab.github.io/nnnotes/commands/)、
  [JSON schema](https://metasekailab.github.io/nnnotes/schemas/)、
  [契约](https://metasekailab.github.io/nnnotes/contracts/)、[stage](https://metasekailab.github.io/nnnotes/stages/)、
  [视图](https://metasekailab.github.io/nnnotes/views/)。
- 原理：[架构](https://metasekailab.github.io/nnnotes/architecture/)、
  [确定性](https://metasekailab.github.io/nnnotes/determinism/)、
  [验证](https://metasekailab.github.io/nnnotes/validation/)。

在同一套环境下导出是确定的：同样的输入，在 nnnotes、Python 依赖库（UnityPy、Pillow、numpy 等）和外部工具版本都相同时，
导出文件逐字节相同；JSON 一律 UTF-8、LF 换行，非有限数写作 `1e999`。版本不同时，相同内容可能写成不同的字节（不同版本的
Pillow 写出的 PNG 像素相同，字节可能不同）。

## 版本记录与版本号

每个版本的改动见 [CHANGELOG.md](https://github.com/MetaSekaiLab/nnnotes/blob/main/CHANGELOG.md)。版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)，约束的接口是命令行、配置键、JSON schema 和文档中的 Python API。

## License

MIT，见 [LICENSE](https://github.com/MetaSekaiLab/nnnotes/blob/main/LICENSE)。
