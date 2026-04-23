# wechat-export-macos

macOS 微信聊天记录一键解密导出工具。从运行中的微信进程提取数据库密钥，解密本地 SQLite 数据库，导出为可读的 TXT / CSV / JSON 格式。

支持 **WeChat 4.x**（新版微信），适用于 **macOS (Apple Silicon & Intel)**。

## 原理

```
微信进程内存 ──提取密钥──▶ all_keys.json ──解密──▶ 明文 SQLite ──导出──▶ TXT/CSV/JSON
   (WCDB/SQLCipher 4)       (AES-256-CBC)          (.db files)         (chat history)
```

微信使用 [WCDB](https://github.com/nicklockwood/wcdb)（基于 SQLCipher 4）加密本地数据库。每个 `.db` 文件有独立的 AES-256 密钥，缓存在进程内存中，格式为 `x'<64hex_key><32hex_salt>'`。本工具通过 Mach VM API 扫描微信进程内存，匹配密钥模式并验证正确性，然后逐页解密数据库。

## 快速开始

### 前置条件

- macOS 11+
- 微信桌面版 4.x（已登录，聊天记录已同步）
- Python 3.8+
- Xcode Command Line Tools：`xcode-select --install`

### 第一步：重签微信（去掉 Hardened Runtime）

```bash
# 先退出微信，然后执行：
sudo codesign --force --deep --sign - /Applications/WeChat.app

# 重新打开微信并登录
```

> **为什么需要重签？** macOS 微信默认带有 Hardened Runtime，阻止其他进程通过 `task_for_pid` 读取其内存。重签为 ad-hoc 签名后移除了这个限制。微信更新后需要重新执行此步骤。
>
> **替代方案：** 关闭 SIP（`csrutil disable`），则无需重签，但影响范围更大。

### 第二步：编译密钥扫描器

```bash
cc -O2 -o find_all_keys_macos find_all_keys_macos.c -framework Foundation
```

### 第三步：提取密钥

```bash
# 确保微信正在运行且已登录
sudo ./find_all_keys_macos
```

输出 `all_keys.json`，包含所有数据库的解密密钥。

### 第四步：安装 Python 依赖 & 解密

```bash
pip3 install -r requirements.txt

# 首次运行会自动检测微信数据目录并生成 config.json
python3 decrypt_db.py
```

`requirements.txt` 当前包含：

- `pycryptodome`：数据库解密
- `Pillow`：图片内容识别与重编码，统一导出为 `jpg` / `png` / `gif`
- `silk-python`：将微信语音 Silk 数据解码并导出为 `wav`

解密后的数据库在 `./decrypted/` 目录下。

### 第五步：导出聊天记录

```bash
# 列出所有会话（按消息数排序）
python3 export_chat.py --list

# 导出某个联系人的聊天记录（模糊搜索昵称/备注）
python3 export_chat.py --name "张三" --output ~/Downloads/张三

# 直接指定用户名导出
python3 export_chat.py --username wxid_xxxxx --output ~/Downloads/output

# 指定自己的 wxid（可选，通常自动检测）
python3 export_chat.py --name "张三" --my-wxid wxid_xxxxx --output ~/Downloads/张三
```

### 一键导出全部会话（Chatlab JSON）

```bash
python3 export_all_in_one.py --output ~/Downloads/wechat-export
```

- 输出目录中每个会话对应一个 JSON 文件
- JSON 结构与 Chatlab 风格示例一致（`chatlab` / `meta` / `members` / `messages`）
- 文件名优先使用联系人或群名称；同名冲突时自动追加 `-2`、`-3` 等后缀
- 图片消息和音频消息会额外导出到输出目录下的 `images/`、`audio/` 子目录
- 对应消息的 JSON 会写入相对路径字段 `file_path`
- 媒体会按内容规范化导出：普通照片 -> `jpg`，静态透明图/贴纸类图片 -> `png`，动画图片 -> `gif`，语音 -> `wav`

### 传统单会话导出（`export_chat.py`）输出文件

当使用 `python3 export_chat.py --name ...` 或 `--username ...` 导出单个会话时，会生成：
- `chat.txt` — 纯文本，可直接阅读
- `chat.csv` — 表格格式，可用 Excel/Numbers 打开
- `chat.json` — 结构化 JSON，适合编程分析
- `images/` — 当前会话中导出的图片文件
- `audio/` — 当前会话中导出的音频文件

其中 `chat.json` 里的图片/音频消息会包含相对导出目录的 `file_path` 字段。导出后的媒体后缀会按内容统一：

- 普通照片 -> `images/<session_hash>/xxx.jpg`
- 静态透明图、表情包、贴纸类图片 -> `images/<session_hash>/xxx.png`
- 动画图片 -> `images/<session_hash>/xxx.gif`
- 语音 -> `audio/<session_hash>/xxx.wav`

## 聊天记录迁移提示

Mac 微信默认只保留在 Mac 上收发的消息。如果需要手机上的历史记录：

1. **手机微信** → 设置 → 通用 → 聊天记录迁移与备份 → **迁移到电脑**
2. 选择要迁移的聊天和时间范围
3. Mac 微信扫码接收
4. 迁移完成后重新执行第三步（提取密钥）和第四步（解密）

## 文件说明

| 文件 | 说明 |
|------|------|
| `find_all_keys_macos.c` | C 语言密钥扫描器，通过 Mach VM API 读取微信进程内存 |
| `decrypt_db.py` | 数据库解密器，逐页解密 SQLCipher 4 加密的数据库 |
| `export_chat.py` | 聊天记录导出工具，支持按联系人导出为 TXT/CSV/JSON |
| `config.py` | 配置加载器，自动检测微信数据目录 |
| `key_utils.py` | 密钥工具函数 |

## 数据库结构

解密后的数据库位于 `./decrypted/`：

```
decrypted/
├── contact/contact.db          # 联系人（昵称、备注、头像URL等）
├── session/session.db          # 会话列表
├── message/message_0.db        # 聊天记录（按时间分片）
├── message/message_1.db
├── message/message_2.db
├── message/message_fts.db      # 全文搜索索引
├── message/media_0.db          # 语音消息
├── sns/sns.db                  # 朋友圈
├── favorite/favorite.db        # 收藏
├── emoticon/emoticon.db        # 表情包
└── ...
```

每个联系人/群的消息存在以 `Msg_<md5(username)>` 命名的表中。

## 常见问题

**Q: `task_for_pid failed` 怎么办？**
确保：(1) 用 `sudo` 运行；(2) 微信已重签（见第一步）；(3) 微信正在运行。

**Q: 微信更新后还能用吗？**
微信更新会恢复原始签名，重新执行第一步（重签）即可。

**Q: 导出的消息中为什么有 `[压缩内容]`？**
微信 WCDB 对部分消息内容使用了 zstd 压缩（`WCDB_CT_message_content=4`），需要额外解压。大部分文本消息不受影响。

**Q: 图片/视频/语音怎么导出？**
图片消息和音频消息会导出到输出目录下的 `images/`、`audio/` 子目录，并在对应 JSON 消息中写入 `file_path`。图片会按内容统一为 `jpg` / `png` / `gif`，语音会统一为 `wav`。如果某条媒体消息找不到源文件、图片无法解码，或语音转码失败，导出不会中断，但该消息的 `file_path` 会为空字符串。视频/其他媒体类型目前仍以消息记录为主，不保证导出文件本体。

**Q: 支持群聊吗？**
支持。群聊的表名是 `Msg_<md5(chatroom_id)>`，导出方式相同。群聊中会显示每个发言者的 wxid（后续可关联到昵称）。

## 致谢

- [L1en2407/wechat-decrypt](https://github.com/L1en2407/wechat-decrypt) — C 语言内存扫描器
- [Thearas/wechat-db-decrypt-macos](https://github.com/Thearas/wechat-db-decrypt-macos) — macOS lldb 密钥提取方案
- [ylytdeng/wechat-decrypt](https://github.com/ylytdeng/wechat-decrypt) — 原始内存搜索方案

## License

WTFPL - Do What The Fuck You Want To Public License.
