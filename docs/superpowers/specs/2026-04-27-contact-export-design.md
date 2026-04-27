# Contact Export Design

## 背景

当前仓库已经依赖 `decrypted/contact/contact.db` 完成联系人搜索、显示名映射和批量会话命名，但还没有提供“一等公民”的通讯录导出能力。

issue #4 需要新增联系人导出功能，在现有解密结果之上直接复用 `contact.db`，导出可备份、可分析的联系人元数据。

## 目标

- 新增独立脚本 `export_contacts.py`
- 默认命令形态为 `python3 export_contacts.py --output <dir>`
- 同时导出 `contacts.json` 和 `contacts.csv`
- `contacts.json` 顶层使用 `meta` + `contacts` 结构
- 默认只导出“真实联系人”，而不是 `contact` 表中的全部记录
- 导出时动态读取 `contact` 表结构，尽量保留本地数据库中的全部可用字段
- 在完整保留原始字段的同时，提供稳定的标准化字段，方便后续程序分析

## 非目标

- 本次不把联系人导出合并进 `export_chat.py`
- 本次不尝试解析额外数据库来补充标签、分组或头像文件本体
- 本次不承诺跨不同微信版本统一所有字段语义，只保证“动态导出原始列 + 项目内定义的标准化字段”
- 本次不默认导出群聊、系统号、自身账号等非“真实联系人”记录

## 方案对比

### 方案 A：只导出固定字段

优点：

- 实现简单
- CSV 列稳定

缺点：

- 会丢失 `contact` 表中机型差异或版本差异带来的额外字段
- 不满足 issue 中“尽量导出其它联系人资料字段”的要求

结论：

不采用。

### 方案 B：动态导出全部原始列

优点：

- 信息最完整
- 与实际数据库结构最一致

缺点：

- 缺少稳定的程序消费字段
- CSV/JSON 的字段体验偏原始，不利于后续自动分析

结论：

不单独采用。

### 方案 C：标准化字段 + 动态原始列混合导出

优点：

- 兼顾稳定性和完整性
- 最贴近 issue 的“默认导出所有列，并对常见字段做标准化”要求

缺点：

- 导出对象结构比纯原始表更复杂一点

结论：

采用本方案。

## 最终方案

### CLI 形态

新增独立脚本：

```bash
python3 export_contacts.py --output ~/Downloads/wechat-contacts
```

首版仅提供最小必要参数：

- `--output`：输出目录，默认 `./exported_contacts`

后续如果需要扩展“导出全部记录”，再增加 `--include-all`；这不是本次首版必需能力。

### 输入数据来源

- 使用现有 `load_config()` 读取 `decrypted_dir`
- 复用现有 `resolve_contact_db_path(decrypted_dir)` 定位 `contact.db`
- 通过 `PRAGMA table_info(contact)` 动态读取 `contact` 表所有列
- 通过 `SELECT * FROM contact` 读取全部行

### 联系人筛选规则

默认仅保留“真实联系人”。筛选规则按保守策略执行：

- 排除 `username` 为空的记录
- 排除 `username` 以 `@chatroom` 结尾的群聊
- 排除当前登录账号本人
- 排除明显系统/服务账号：
  - `weixin`
  - `medianote`
  - `fmessage`
  - `floatbottle`
  - `filehelper`
  - `newsapp`
  - `notifymessage`
  - `gh_*`

这里采用“保守过滤”而非激进过滤，避免误删真人联系人。除上述显式规则外，其余记录尽量保留。

### 自身账号识别

自身账号优先复用现有 `export_chat.py` 中的 `detect_my_wxid()` 逻辑。

如果自动识别失败，则不额外删除任何 `wxid_*` 记录，避免误删。

### 标准化字段

每个导出的联系人对象都包含以下稳定字段：

- `username`
- `display_name`
- `nick_name`
- `remark`
- `alias`
- `contact_type`
- `is_chatroom`

字段规则：

- `display_name = remark or nick_name or alias or username`
- `contact_type` 优先取 `local_type`，不存在时为空
- `is_chatroom` 基于 `username.endswith("@chatroom")` 判断

### 原始字段保留方式

除了标准化字段外，还会把 `contact` 表中当前实际存在的所有列逐列保留到导出对象中。

如果某个标准化字段与原始列语义重叠：

- 标准化字段继续保留在顶层，作为稳定消费字段
- 原始列仍按原列名保留，避免信息损失

### JSON 输出结构

`contacts.json` 顶层结构：

```json
{
  "meta": {
    "version": "0.0.1",
    "exportedAt": 1777248000,
    "generator": "wechat-export-macos",
    "source": "decrypted/contact/contact.db",
    "filterMode": "real_contacts",
    "contactCount": 123
  },
  "contacts": [
    {
      "username": "wxid_friend",
      "display_name": "老张",
      "nick_name": "张三",
      "remark": "老张",
      "alias": "",
      "contact_type": 1,
      "is_chatroom": false,
      "local_type": 1,
      "big_head_img_url": "https://..."
    }
  ]
}
```

`meta.source` 写入解析时使用的 `contact.db` 路径，便于追溯来源。

### CSV 输出结构

`contacts.csv` 每行一个联系人。

列顺序固定为：

1. 标准化字段
2. 其余原始列，按 `PRAGMA table_info(contact)` 返回顺序追加

这样可以保证：

- 常用字段总在前面
- 原始表结构依然完整保留

### 模块职责

建议把联系人导出能力集中在新模块 `export_contacts.py` 内，保持首版实现清晰：

- 运行时路径解析
- `contact` 表结构读取
- 联系人筛选
- 标准化
- JSON/CSV 写出
- CLI 参数处理

如果后续 `chat_export.py` 也要复用联系人筛选逻辑，再按实际需要抽共享 helper；本次先避免过早抽象。

## 错误处理

- 找不到 `contact.db` 时，CLI 直接报错并退出非零状态
- `contact` 表不存在时，CLI 直接报错并退出非零状态
- 输出目录创建失败时，CLI 直接报错并退出非零状态
- 单个字段值为 `NULL` 时，按 Python `None` 参与 JSON 序列化；CSV 中写为空字符串

这是一个“整体数据集导出”能力，因此与聊天消息导出不同，首版不做“跳过坏记录继续导”的复杂容错。

## 测试策略

新增测试覆盖以下行为：

- 通过动态表结构导出额外原始列
- JSON 输出为 `meta` + `contacts` 结构
- CSV 同时包含标准化字段和额外原始列
- 默认过滤空用户名、群聊、自身账号、系统账号
- `display_name` 按 `remark > nick_name > alias > username` 优先级生成

测试方式沿用当前仓库风格，使用临时目录创建最小 `contact.db` 样本进行断言。

## 验收标准

- 运行 `python3 export_contacts.py --output <dir>` 后会生成 `contacts.json` 与 `contacts.csv`
- JSON 顶层包含 `meta` 和 `contacts`
- 默认导出结果不包含群聊、本人和明显系统号
- 每个联系人同时包含标准化字段和 `contact` 表原始字段
- 测试全部通过
