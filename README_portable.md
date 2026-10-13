# ArxivCrawler v0.14.0 — 可移植版（portable）

无需安装、双击即用。把整个 zip 解压到任意目录即可（路径最好**不含中文/空格**）。

## 1. 装依赖（首次运行）

* **Windows**：双击 `install.bat`（需已装 Python 3.10-3.12）
* **Linux/macOS**：`bash install.sh`

它会在当前目录建一个 `.venv` 虚拟环境并装好所有依赖。

## 2. 启动

* **主程序**（普通用户）：`launch_desktop.bat` / `./launch_desktop.sh`
  - 首次启动会弹"激活"对话框；把请求码发给授权方，拿到激活码粘贴进去即可
* **激活码生成器**（仅授权方）：`launch_generator.bat` / `./launch_generator.sh`
  - 需要把 `license_keys\private_key.pem` 放在**本目录**下（或在 GUI 里点"选择…"指定）
  - 私钥**绝对不要**通过本 zip 共享；只放在授权方自己机器上

## 3. 离线激活流程

1. 用户在自己机器的「主程序」上点开"激活"对话框 → 复制**本机设备请求码**发给你
2. 你在自己机器上启动「激活码生成器」→ 粘贴请求码 → 点"生成激活码" → 把激活码发回给用户
3. 用户粘贴回"激活"对话框 → 激活成功

每张激活码只能在该设备上**激活一次**。同一台机器重装/换码激活会消耗一张新码。

## 4. 数据存储

* 论文数据库：%LOCALAPPDATA%\ArxivCrawler\papers.db（Windows）/ ~/.local/share/ArxivCrawler/（其他）
* 授权状态：%LOCALAPPDATA%\ArxivCrawler\license.dat
* 已用 serial 记录：%LOCALAPPDATA%\ArxivCrawler\used_serials.json
* 桌面软件 + GUI 生成器数据互不影响

## 5. 卸载

直接删除解压目录即可（数据在 %LOCALAPPDATA%\ArxivCrawler\，不会自动删，想删一起删）。

## 6. 常见问题

**Q: 启动后弹"未激活"？**
A: 正常，复制对话框里的"本机设备请求码"发给授权方即可。

**Q: 找不到私钥？**
A: GUI 生成器启动时会自动在以下位置找 `private_key.pem`：
  * exe 同目录 `license_keys\private_key.pem`
  * exe 同目录 `private_key.pem`
  * 启动时点"选择…"手动指定

**Q: 如何批量签发？**
A: 命令行 `python generate_license.py <请求码1> <请求码2> ...`
