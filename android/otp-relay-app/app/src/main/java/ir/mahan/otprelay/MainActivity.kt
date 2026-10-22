package ir.mahan.otprelay

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast

class MainActivity : Activity() {
    private lateinit var preferences: RelayPreferences
    private lateinit var connectionButton: Button
    private lateinit var sim1Input: EditText
    private lateinit var sim2Input: EditText
    private lateinit var keywordInput: EditText
    private lateinit var enabledSwitch: Switch
    private lateinit var consentCheck: CheckBox
    private val handler = Handler(Looper.getMainLooper())
    private val statusRefresh = object : Runnable {
        override fun run() {
            renderStatus()
            handler.postDelayed(this, 2_000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        preferences = RelayPreferences(this)
        window.statusBarColor = Color.rgb(245, 251, 250)
        window.navigationBarColor = Color.rgb(245, 251, 250)
        buildUi()
        loadValues()
        requestRequiredPermissions()
        if (preferences.isReady()) RelayForegroundService.start(this)
    }

    override fun onResume() {
        super.onResume()
        if (preferences.isReady()) RelayForegroundService.wake(this)
        handler.post(statusRefresh)
    }

    override fun onPause() {
        handler.removeCallbacks(statusRefresh)
        super.onPause()
    }

    private fun buildUi() {
        val scroll = ScrollView(this)
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            layoutDirection = ViewGroup.LAYOUT_DIRECTION_RTL
            setPadding(dp(20), dp(26), dp(20), dp(36))
            setBackgroundColor(Color.rgb(245, 251, 250))
        }
        scroll.addView(container)

        container.addView(TextView(this).apply {
            text = "رابط پایدار پیامک و MahanBot"
            textSize = 24f
            setTextColor(Color.rgb(0, 105, 100))
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, dp(8))
        }, matchWrap())

        container.addView(TextView(this).apply {
            text = "سرور ثابت: ${RelayPreferences.SERVER_BASE_URL}\nبدون کلید دستی؛ ارتباط با شناسه خودکار دستگاه انجام می‌شود."
            textSize = 14f
            gravity = Gravity.CENTER
            setTextColor(Color.DKGRAY)
            setPadding(0, 0, 0, dp(18))
        }, matchWrap())

        connectionButton = Button(this).apply {
            textSize = 16f
            gravity = Gravity.CENTER
            isAllCaps = false
            setTextColor(Color.WHITE)
            setPadding(dp(14), dp(14), dp(14), dp(14))
            setOnClickListener { testConnection() }
        }
        container.addView(connectionButton, matchWrap())

        container.addView(TextView(this).apply {
            text = "برای بررسی فوری ارتباط، دکمه وضعیت را لمس کنید."
            textSize = 12f
            gravity = Gravity.CENTER
            setTextColor(Color.GRAY)
            setPadding(0, dp(5), 0, 0)
        }, matchWrap())

        sim1Input = input("کد ملی سیم‌کارت ۱", numeric = true)
        sim2Input = input("کد ملی سیم‌کارت ۲ (اختیاری)", numeric = true)
        keywordInput = input("عبارت الزامی داخل پیامک", numeric = false)
        container.addView(sim1Input, matchWrap(top = 18))
        container.addView(sim2Input, matchWrap(top = 10))
        container.addView(keywordInput, matchWrap(top = 10))

        consentCheck = CheckBox(this).apply {
            text = "تأیید می‌کنم فقط پیامک منطبق با عبارت بالا و OTP آن برای کد ملی تنظیم‌شده به سرور ارسال شود."
            textSize = 14f
        }
        container.addView(consentCheck, matchWrap(top = 14))

        enabledSwitch = Switch(this).apply {
            text = "فعال‌سازی ارتباط پایدار و انتظار برای پیامک"
            textSize = 16f
        }
        container.addView(enabledSwitch, matchWrap(top = 10))

        val saveButton = Button(this).apply {
            text = "ذخیره و شروع ارتباط"
            isAllCaps = false
            setOnClickListener { saveAndStart() }
        }
        container.addView(saveButton, matchWrap(top = 18))

        container.addView(TextView(this).apply {
            text = "سرویس هر ۲۰ ثانیه سرور را بررسی می‌کند، در پس‌زمینه منتظر پیامک می‌ماند و پس از روشن‌شدن گوشی یا به‌روزرسانی اپ دوباره فعال می‌شود."
            textSize = 13f
            gravity = Gravity.CENTER
            setTextColor(Color.GRAY)
            setPadding(0, dp(18), 0, 0)
        }, matchWrap())

        setContentView(scroll)
    }

    private fun loadValues() {
        sim1Input.setText(preferences.nationalIdSim1)
        sim2Input.setText(preferences.nationalIdSim2)
        keywordInput.setText(preferences.keyword)
        enabledSwitch.isChecked = preferences.enabled
        consentCheck.isChecked = preferences.consentAccepted
        renderStatus()
    }

    private fun saveAndStart() {
        val sim1 = RelayPreferences.validNationalId(sim1Input.text.toString())
        val sim2Raw = sim2Input.text.toString().trim()
        val sim2 = RelayPreferences.validNationalId(sim2Raw)
        if (sim1 == null && sim2 == null) {
            toast("حداقل یک کد ملی ۱۰ رقمی وارد کنید")
            return
        }
        if (sim2Raw.isNotBlank() && sim2 == null) {
            toast("کد ملی سیم‌کارت ۲ باید ۱۰ رقمی باشد")
            return
        }
        if (enabledSwitch.isChecked && !consentCheck.isChecked) {
            toast("برای فعال‌سازی، رضایت ارسال OTP را تأیید کنید")
            return
        }
        preferences.nationalIdSim1 = sim1.orEmpty()
        preferences.nationalIdSim2 = sim2.orEmpty()
        preferences.keyword = keywordInput.text.toString().ifBlank { RelayPreferences.DEFAULT_KEYWORD }
        preferences.consentAccepted = consentCheck.isChecked
        preferences.enabled = enabledSwitch.isChecked
        if (preferences.isReady()) {
            requestRequiredPermissions()
            RelayForegroundService.start(this)
            preferences.lastStatus = "در حال برقراری ارتباط با سرور…"
            toast("ارتباط پایدار فعال شد")
            handler.postDelayed({ testConnection() }, 700)
        } else {
            preferences.lastStatus = "ارتباط غیرفعال است"
            stopService(android.content.Intent(this, RelayForegroundService::class.java))
            toast("تنظیمات ذخیره شد")
        }
        renderStatus()
    }

    private fun testConnection() {
        val nationalId = RelayPreferences.validNationalId(sim1Input.text.toString())
            ?: RelayPreferences.validNationalId(sim2Input.text.toString())
        if (nationalId == null) {
            toast("ابتدا یک کد ملی معتبر وارد کنید")
            return
        }
        preferences.lastStatus = "در حال آزمایش ارتباط اپ، سرور و MahanBot…"
        renderStatus()
        RelayForegroundService.wake(this)
        Thread {
            val result = RelayApi.checkStatus(DeviceIdentity.getOrCreate(this), nationalId)
            runOnUiThread {
                val message = when {
                    result.success && result.waiting == true -> "متصل؛ MahanBot منتظر پیامک است"
                    result.success -> "متصل به سرور؛ منتظر درخواست OTP از MahanBot"
                    result.code == -1 -> "ارتباط شبکه برقرار نیست؛ تلاش مجدد خودکار"
                    else -> "ارتباط ناموفق: HTTP ${result.code}"
                }
                preferences.lastStatus = message
                renderStatus()
            }
        }.start()
    }

    private fun requestRequiredPermissions() {
        val missing = buildList {
            if (checkSelfPermission(Manifest.permission.RECEIVE_SMS) != PackageManager.PERMISSION_GRANTED) {
                add(Manifest.permission.RECEIVE_SMS)
            }
            if (Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            ) {
                add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        if (missing.isNotEmpty()) requestPermissions(missing.toTypedArray(), REQUEST_PERMISSIONS)
    }

    private fun renderStatus() {
        val status = preferences.lastStatus
        val ageSeconds = if (preferences.lastStatusAt > 0) {
            (System.currentTimeMillis() - preferences.lastStatusAt).coerceAtLeast(0L) / 1000
        } else {
            Long.MAX_VALUE
        }
        val freshHeartbeat = ageSeconds <= STATUS_FRESH_SECONDS
        val connected = freshHeartbeat && (
            status.startsWith("متصل") ||
                status.contains("با موفقیت تحویل سرور شد")
            )
        val connecting = freshHeartbeat && (
            status.contains("در حال") ||
                status.contains("تلاش مجدد") ||
                status.contains("در صف")
            )

        val color = when {
            connected -> Color.rgb(18, 142, 82)
            connecting -> Color.rgb(239, 145, 20)
            !preferences.enabled -> Color.rgb(117, 117, 117)
            else -> Color.rgb(198, 50, 50)
        }
        val marker = when {
            connected -> "🟢"
            connecting -> "🟠"
            !preferences.enabled -> "⚪"
            else -> "🔴"
        }
        val ageText = if (ageSeconds == Long.MAX_VALUE) "هنوز بررسی نشده" else "$ageSeconds ثانیه قبل"
        connectionButton.backgroundTintList = ColorStateList.valueOf(color)
        connectionButton.text = "$marker ${connectionTitle(status, connected, connecting)}\n$status\nآخرین بررسی: $ageText"
    }

    private fun connectionTitle(status: String, connected: Boolean, connecting: Boolean): String = when {
        connected && status.contains("MahanBot منتظر") -> "ارتباط اپ، سرور و MahanBot برقرار است"
        connected -> "ارتباط اپ و سرور برقرار است"
        connecting -> "در حال برقراری ارتباط"
        !preferences.enabled -> "ارتباط غیرفعال است"
        else -> "ارتباط قطع است"
    }

    private fun input(hint: String, numeric: Boolean): EditText = EditText(this).apply {
        this.hint = hint
        textSize = 17f
        gravity = Gravity.CENTER_VERTICAL or Gravity.END
        setPadding(dp(14), dp(12), dp(14), dp(12))
        inputType = if (numeric) {
            InputType.TYPE_CLASS_NUMBER
        } else {
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
        }
        if (numeric) maxLines = 1
    }

    private fun matchWrap(top: Int = 0): LinearLayout.LayoutParams = LinearLayout.LayoutParams(
        LinearLayout.LayoutParams.MATCH_PARENT,
        LinearLayout.LayoutParams.WRAP_CONTENT,
    ).apply {
        topMargin = dp(top)
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    companion object {
        private const val REQUEST_PERMISSIONS = 280
        private const val STATUS_FRESH_SECONDS = 65L
    }
}
