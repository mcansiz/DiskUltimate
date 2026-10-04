# DiskUltimate zh_CN sozlugu (DiskGenius terminolojisi esas)

| English | 简体中文 |
|---|---|
| disk | 磁盘 |
| physical disk | 物理磁盘 |
| system disk / OS disk | 系统磁盘 |
| disk image / image file | 磁盘镜像 / 镜像文件 |
| virtual disk | 虚拟磁盘 |
| partition | 分区 |
| partition table | 分区表 |
| primary / extended / logical partition | 主分区 / 扩展分区 / 逻辑分区 |
| volume | 卷 |
| file system | 文件系统 |
| format | 格式化 |
| sector | 扇区 |
| cluster | 簇 |
| block | 块 |
| label (volume) | 卷标 |
| free space / unallocated | 空闲空间 / 未分配 |
| resize | 调整大小 |
| move | 移动 |
| backup / back up | 备份 |
| restore | 还原 |
| clone | 克隆 |
| wipe / erase | 擦除 |
| secure wipe | 安全擦除 |
| recover / recovery | 恢复 |
| data recovery | 数据恢复 |
| file signature / carving | 文件签名 / 按签名恢复 |
| deleted file | 已删除文件 |
| lost partition | 丢失的分区 |
| hex viewer | 十六进制查看器 |
| alignment (4K) | 4K 对齐 |
| pending operations | 待执行操作 |
| step | 步骤 |
| Apply | 应用 |
| destructive | 破坏性 |
| read-only | 只读 |
| mount / mounted | 挂载 / 已挂载 |
| drive letter | 驱动器号 |
| boot / bootable | 引导 / 可引导 |
| boot code | 引导代码 |
| bootloader | 引导程序 |
| boot entry (UEFI) | 启动项 |
| firmware | 固件 |
| mainboard NVRAM | 主板非易失性存储器 (NVRAM) |
| privileges / administrator | 权限 / 管理员 |
| elevated privileges | 提升的权限 |
| export / import | 导出 / 导入 |
| folder / directory | 文件夹 / 目录 |
| directory entry | 目录项 |
| record (MFT) | 记录 |
| superblock | 超级块 |
| inode | inode |
| journal | 日志 |
| bitmap | 位图 |
| encrypted | 加密 |
| signature | 签名 |
| interface | 界面 |
| diagnostics | 诊断 |
| freeze (UI) | 界面冻结 |
| log | 日志 |
| settings | 设置 |
| device | 设备 |
| corrupt / damaged | 损坏 |
| not supported | 不支持 |
| Fast Startup | 快速启动 |
| checksum | 校验和 |
| UUID / GUID | UUID / GUID |
| mnemonic | 中文词 + (&X) |
| quotes | “ ” |
| move back / forward (partition) | 向磁盘起始方向移动 / 向磁盘末尾方向移动 |
| hibernation file | 休眠文件 |
| dirty flag | 脏标志 |
| data run (NTFS) | 数据运行 |
| extent | 区段 |
| allocation group (XFS) / group (ext) | 分配组 / 块组 |
| stack dump | 堆栈转储 |
| Restart as {} | 以{}身份重新启动 |

Notlar:
- "NEW VOLUME" (#1475) varsayilan birim etiketi olarak ASCII birakildi: FAT etiketi
  latin-1 ile kodlanir, Cince karakterler "?" olurdu.
- "recovered_{:012X}.{}" (#1702) dosya adi kalibi, ASCII birakildi.
- Qt dosya suzgecleri (`;;`, `(*.img)`) ASCII kalir.
