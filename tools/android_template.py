"""Готує шаблон збірки Flet для Android: висока частота екрана й встановлення оновлень.

Багато Android-телефонів (Samsung, Xiaomi тощо) тримають застосунки на 60 Гц, поки ті самі
не попросять вищу частоту, а Flutter цього не робить. Скрипт завантажує стандартний шаблон
Flet потрібної версії й замінює MainActivity: та обирає режим дисплея з найбільшою частотою
(за тієї ж роздільності) — 90/120/144 Гц, якщо екран їх підтримує.

MainActivity також тримає MulticastLock — для пошуку Chromecast у Wi-Fi.

Також MainActivity відкриває системне вікно встановлення для завантаженого застосунком
оновлення (посилання uafixupdate://install, файл cache/update.apk).

Використання (CI):  python tools/android_template.py <тека>  →  flet build apk --template <тека>/build
"""
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

import flet.version

TEMPLATE_URL = "https://github.com/flet-dev/flet/releases/download/v{v}/flet-build-template.zip"

MAIN_ACTIVITY = '''package {{ cookiecutter.org_name_2 }}.{{ cookiecutter.package_name }}

import android.app.AlarmManager
import android.app.PendingIntent
import android.app.PictureInPictureParams
import android.content.ComponentName
import android.content.pm.PackageManager
import android.util.Rational
import androidx.annotation.RequiresApi
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.core.content.FileProvider
import io.flutter.embedding.android.FlutterFragmentActivity
import java.io.File

class MainActivity : FlutterFragmentActivity() {
    private var multicastLock: WifiManager.MulticastLock? = null
    /** «Картинка в картинці»: дозволено, поки відкритий плеєр (вмикає Python-частина
     *  посиланням uafixupdate://pip?on=1 / on=0). */
    private var pipAllowed = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestHighRefreshRate()
        handleInstallIntent(intent)
        // без цього Android відкидає multicast-пакети, і пошук Chromecast (mDNS) нічого не бачить
        try {
            val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            multicastLock = wifi.createMulticastLock("uafix-cast").apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (e: Exception) {
        }
    }

    override fun onDestroy() {
        multicastLock?.let { if (it.isHeld) it.release() }
        super.onDestroy()
    }

    override fun onResume() {
        super.onResume()
        requestHighRefreshRate()
    }

    /** Вихід на домашній екран під час перегляду — відео в плаваюче вікно. */
    override fun onUserLeaveHint() {
        super.onUserLeaveHint()
        if (pipAllowed && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            try {
                enterPictureInPictureMode(pipParams())
            } catch (e: Exception) {
            }
        }
    }

    @RequiresApi(Build.VERSION_CODES.O)
    private fun pipParams(): PictureInPictureParams {
        val b = PictureInPictureParams.Builder().setAspectRatio(Rational(16, 9))
        // Android 12+: плавний перехід у плаваюче вікно жестом «додому»
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) b.setAutoEnterEnabled(pipAllowed)
        return b.build()
    }

    private fun openExternal(url: String?, title: String?) {
        if (url == null || !(url.startsWith("https://") || url.startsWith("http://"))) return
        val view = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(Uri.parse(url), "application/x-mpegURL")
            putExtra("title", title ?: "")
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try {
            startActivity(Intent.createChooser(view, title ?: "Відкрити в плеєрі")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: Exception) {
            try {  // без вікна вибору (на деяких телевізорах його немає)
                startActivity(Intent(view).setDataAndType(Uri.parse(url), "video/*"))
            } catch (e2: Exception) {
            }
        }
    }

    private fun setPip(on: Boolean) {
        pipAllowed = on && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            packageManager.hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            try {
                setPictureInPictureParams(pipParams())
            } catch (e: Exception) {
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        if (handleInstallIntent(intent)) return
        super.onNewIntent(intent)
    }

    /** Найвища частота оновлення за поточної роздільності (інакше система може тримати 60 Гц). */
    private fun requestHighRefreshRate() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        @Suppress("DEPRECATION")
        val display = (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) display
                       else windowManager.defaultDisplay) ?: return
        val current = display.mode
        val best = display.supportedModes
            .filter { it.physicalWidth == current.physicalWidth && it.physicalHeight == current.physicalHeight }
            .maxByOrNull { it.refreshRate } ?: return
        val params = window.attributes
        if (params.preferredDisplayModeId != best.modeId) {
            params.preferredDisplayModeId = best.modeId
            window.attributes = params
        }
    }

    /** Іконка застосунку: кожен варіант — свій «вхід» (activity-alias) у лаунчері; вмикаємо
     *  вибраний і вимикаємо решту. Спершу — новий, щоб застосунок не зник з лаунчера. */
    private fun setIcon(name: String) {
        val all = listOf("Classic", "Reel", "Mask", "Bolt")
        val want = all.firstOrNull { it.equals(name, ignoreCase = true) } ?: return
        val base = javaClass.name.substringBeforeLast('.')  // пакет коду (не applicationId)
        val pm = packageManager
        try {
            pm.setComponentEnabledSetting(ComponentName(this, "$base.Icon$want"),
                PackageManager.COMPONENT_ENABLED_STATE_ENABLED, PackageManager.DONT_KILL_APP)
            for (n in all) if (n != want) {
                pm.setComponentEnabledSetting(ComponentName(this, "$base.Icon$n"),
                    PackageManager.COMPONENT_ENABLED_STATE_DISABLED, PackageManager.DONT_KILL_APP)
            }
        } catch (e: Exception) {
        }
    }

    /** Перезапуск застосунку: запланувати запуск через пів секунди й завершити процес
     *  (Python усередині процесу інакше не почне заново). */
    private fun restartApp() {
        val launch = packageManager.getLaunchIntentForPackage(packageName) ?: return
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        val pending = PendingIntent.getActivity(
            this, 0, launch, PendingIntent.FLAG_CANCEL_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val alarm = getSystemService(Context.ALARM_SERVICE) as AlarmManager
        alarm.set(AlarmManager.RTC, System.currentTimeMillis() + 500, pending)
        finishAffinity()
        Runtime.getRuntime().exit(0)
    }

    /**
     * uafixupdate://install — Python-частина завантажила оновлення в cache/update.apk і просить
     * відкрити системне вікно встановлення. Береться лише цей фіксований файл із кешу самого
     * застосунку, тож стороннє посилання не змусить встановити щось інше.
     */
    private fun handleInstallIntent(intent: Intent?): Boolean {
        val data = intent?.data ?: return false
        if (data.scheme != "uafixupdate") return false
        if (data.host == "restart") {  // напр. після зміни теми: запуск заново за мить
            restartApp()
            return true
        }
        if (data.host == "pip") {  // плеєр відкрито / закрито
            setPip(data.getQueryParameter("on") == "1")
            return true
        }
        if (data.host == "icon") {  // вибір іконки в налаштуваннях
            setIcon(data.getQueryParameter("name") ?: "classic")
            return true
        }
        if (data.host == "external") {  // «Плеєр: інша програма» — вибір плеєра (VLC, MX…)
            openExternal(data.getQueryParameter("url"), data.getQueryParameter("title"))
            return true
        }
        // update.apk кладе Python-частина в кеш (запасні варіанти — дані застосунку)
        val apk = listOf(cacheDir, filesDir)
            .map { File(it, "update.apk") }.firstOrNull { it.isFile } ?: return true
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !packageManager.canRequestPackageInstalls()) {
            // спершу дозволити цьому застосунку встановлювати програми; на частині Android TV
            // такого екрана налаштувань немає — тоді одразу до встановлення (система сама
            // запитає дозвіл)
            try {
                startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                     Uri.parse("package:$packageName")))
                return true
            } catch (e: Exception) {
            }
        }
        try {
            val uri = FileProvider.getUriForFile(this, "$packageName.provider", apk)
            startActivity(Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            })
        } catch (e: Exception) {
        }
        return true
    }
}
'''

# посилання uafixupdate://install відкриває саме цей застосунок (див. handleInstallIntent)
INSTALL_INTENT_FILTER = '''
            <intent-filter>
                <action android:name="android.intent.action.VIEW" />
                <category android:name="android.intent.category.DEFAULT" />
                <category android:name="android.intent.category.BROWSABLE" />
                <data android:scheme="uafixupdate" />
            </intent-filter>'''
LAUNCHER_FILTER = """            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
                <category android:name="android.intent.category.LEANBACK_LAUNCHER" />   <!-- Google TV -->
            </intent-filter>"""
# Іконки на вибір (налаштування): лаунчер бачить застосунок через «входи» (activity-alias),
# кожен зі своєю іконкою й банером TV; увімкнений лише один (див. setIcon у MainActivity)
ICON_VARIANTS = ["reel", "mask", "bolt"]
ICONS_DIR = Path(__file__).with_name("app_icons")
ALIAS = """
        <activity-alias
            android:name=".Icon{name}"
            android:targetActivity=".MainActivity"
            android:exported="true"
            android:enabled="{enabled}"
            android:icon="{icon}"{round}
            android:banner="{banner}">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
                <category android:name="android.intent.category.LEANBACK_LAUNCHER" />
            </intent-filter>
        </activity-alias>"""
ALIASES = ALIAS.format(name="Classic", enabled="true", icon="@mipmap/ic_launcher", round="",
                       banner="@drawable/tv_banner") + "".join(
    ALIAS.format(name=v.capitalize(), enabled="false", icon=f"@mipmap/ic_launcher_{v}",
                 round=f'\n            android:roundIcon="@mipmap/ic_launcher_{v}_round"',
                 banner=f"@drawable/tv_banner_{v}") for v in ICON_VARIANTS)


def copy_icon_resources(res: Path) -> None:
    """Ресурси іконок-варіантів: ic_launcher_<v>*.png / .xml і tv_banner_<v>.png."""
    for v in ICON_VARIANTS:
        for d in sorted((ICONS_DIR / v / "android_res").iterdir()):
            for f in d.iterdir():
                stem = f.stem.replace("ic_launcher", f"ic_launcher_{v}", 1)
                dst = res / d.name / (stem + f.suffix)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if f.suffix == ".xml":  # адаптивна іконка — з перейменованими шарами
                    dst.write_text(f.read_text(encoding="utf-8").replace(
                        "@mipmap/ic_launcher_", f"@mipmap/ic_launcher_{v}_"), encoding="utf-8")
                else:
                    shutil.copy(f, dst)
        for d in sorted((ICONS_DIR / v / "tv_res").iterdir()):
            for f in d.iterdir():
                dst = res / d.name / f"tv_banner_{v}{f.suffix}"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(f, dst)
# «картинка в картинці» (плаваюче вікно з відео при виході з застосунку)
ACTIVITY_NAME_ATTR = 'android:name=".MainActivity"'
PIP_ATTRS = ('android:supportsPictureInPicture="true"\n'
             '            android:resizeableActivity="true"')
# банер для лаунчера Android TV (без нього там лише сірий прямокутник із назвою)
APP_ICON_ATTR = 'android:icon="@mipmap/ic_launcher">'
BANNER = Path(__file__).with_name("tv_banner.png")


def main(out_dir: str) -> None:
    out = Path(out_dir)
    url = TEMPLATE_URL.format(v=flet.version.flet_version)
    print(f"Завантажую {url}")
    with urllib.request.urlopen(url) as resp:
        zipfile.ZipFile(io.BytesIO(resp.read())).extractall(out)
    activities = list(out.glob("build/*/android/app/src/main/kotlin/*/MainActivity.kt"))
    if len(activities) != 1:
        sys.exit(f"MainActivity.kt не знайдено однозначно: {activities}")
    activities[0].write_text(MAIN_ACTIVITY, encoding="utf-8")
    print(f"Оновлено {activities[0]}")

    manifests = list(out.glob("build/*/android/app/src/main/AndroidManifest.xml"))
    if len(manifests) != 1:
        sys.exit(f"AndroidManifest.xml не знайдено однозначно: {manifests}")
    manifest = manifests[0].read_text(encoding="utf-8")
    if LAUNCHER_FILTER not in manifest:
        sys.exit("Не знайдено intent-filter лаунчера в AndroidManifest.xml — шаблон змінився")
    # лаунчер відкриває застосунок через «входи» з іконками (ALIASES), а не напряму
    manifest = manifest.replace(LAUNCHER_FILTER, INSTALL_INTENT_FILTER.lstrip("\n"), 1)
    if "</activity>" not in manifest:
        sys.exit("Не знайдено </activity> в AndroidManifest.xml — шаблон змінився")
    manifest = manifest.replace("</activity>", "</activity>" + ALIASES, 1)
    if ACTIVITY_NAME_ATTR not in manifest:
        sys.exit("Не знайдено MainActivity в AndroidManifest.xml — шаблон змінився")
    manifest = manifest.replace(ACTIVITY_NAME_ATTR,
                                ACTIVITY_NAME_ATTR + "\n            " + PIP_ATTRS, 1)
    if APP_ICON_ATTR not in manifest:
        sys.exit("Не знайдено android:icon у <application> — шаблон змінився")
    manifest = manifest.replace(
        APP_ICON_ATTR, 'android:banner="@drawable/tv_banner"\n        ' + APP_ICON_ATTR, 1)
    shutil.copy(BANNER, manifests[0].parent / "res" / "drawable-xhdpi" / "tv_banner.png")
    copy_icon_resources(manifests[0].parent / "res")
    manifests[0].write_text(manifest, encoding="utf-8")
    print(f"Оновлено {manifests[0]}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "android_template")
