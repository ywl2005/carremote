[app]

# 应用显示名（装在手机上看到的名字）
title = 小车遥控

package.name = carremote
package.domain = org.lyw

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf

version = 1.0

# 依赖：python3 + kivy
requirements = python3,kivy==2.3.0

orientation = portrait
fullscreen = 0

# 必须联网权限
android.permissions = INTERNET

android.api = 33
android.minapi = 21
android.ndk_api = 21
android.archs = arm64-v8a, armeabi-v7a

# 竖屏 + 允许横屏时自适应（0=不锁定）
android.accept_sdk_license = True

p4a.branch = master

[buildozer]
log_level = 2
warn_on_root = 1
