package ir.mahan.otprelay

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
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
    private lateinit var statusView: TextView
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

        statusView = TextView(this).apply {
            textSize = 16f
            gravity = Gravity.CENTER
            setTextColor(Color.rgb(0, 92, 83))
            setBackgroundColor(Color.rgb(223, 246, 242))
            setPadding(dp(14), dp(14), dp(14), dp(14))
        }
        container.addView(statusView, matchWrap())

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
            setOnClickListener { saveAndStart() }
        }
        container.addView(saveButton, matchWrap(top = 18))

        val testButton = Button(this).apply {
            text = "آزمایش اتصال به MahanBot"
            setOnClickListener { testConnection() }
        }
        container.addView(testButton, matchWrap(top = 8))

        container.addView(TextView(this).apply {
            text = "برای پایداری، اعلان دائمی وضعیت نمایش داده می‌شود و سرویس پس از روشن‌شدن گوشی یا به‌روزرسانی اپ دوباره فعال خواهد شد."
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
        statusView.text = "در حال آزمایش اتصال…"
        Thread {
            val result = RelayApi.checkStatus(DeviceIdentity.getOrCreate(this), nationalId)
            runOnUiThread {
                val message = when {
                    result.success && result.waiting == true -> "اتصال برقرار است؛ MahanBot منتظر OTP است"
                    result.success -> "اتصال برقرار است؛ منتظر شروع درخواست از MahanBot"
                    else -> "اتصال ناموفق: HTTP ${result.code}"
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
        val ageSeconds = if (preferences.lastStatusAt > 0) {
            (System.currentTimeMillis() - preferences.lastStatusAt) / 1000
        } else {
            0
        }
        statusView.text = "وضعیت: ${preferences.lastStatus}\nآخرین بررسی: ${ageSeconds}s قبل"
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
    }
}
