; Inno Setup 安装包脚本模板（用户本机装 Inno Setup 后编译出 setup.exe）
; 用法：用 Inno Setup Compiler 打开此文件并编译（需先 build_exe.bat 生成 dist\ArxivCrawler.exe）
[Setup]
AppName=arXiv 论文爬虫
AppVersion=0.10.0
AppPublisher=WorkBuddy
DefaultDirName={autopf}\arXivCrawler
DefaultGroupName=arXiv 论文爬虫
OutputDir=installer
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
DisableProgramGroupPage=yes

[Files]
Source: "dist\ArxivCrawler.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\arXiv 论文爬虫"; Filename: "{app}\ArxivCrawler.exe"
Name: "{userdesktop}\arXiv 论文爬虫"; Filename: "{app}\ArxivCrawler.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Run]
Filename: "{app}\ArxivCrawler.exe"; Description: "启动 arXiv 论文爬虫"; Flags: nowait postinstall skipifsilent
