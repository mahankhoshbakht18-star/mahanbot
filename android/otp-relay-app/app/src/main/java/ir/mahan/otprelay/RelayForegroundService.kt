package ir.mahan.otprelay

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.os.PowerManager
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.min

class RelayForegroundService : Service() {
    private lateinit var preferences: RelayPreferences
    private lateinit var pendingStore: PendingOtpStore
    private lateinit var deviceId: String
    private lateinit var scheduler: ScheduledExecutorService
    private val workInProgress = AtomicBoolean(false)

    override fun onCreate() {
        super.onCreate()
        preferences = RelayPreferences(this)
        pendingStore = PendingOtpStore(this)
        deviceId = DeviceIdentity.getOrCreate(this)
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, notification("در حال اتصال به سرور…"))
        scheduler = Executors.newSingleThreadScheduledExecutor()
        scheduler.scheduleWithFixedDelay({ heartbeatAndDrainSafe() }, 0, HEARTBEAT_SECONDS, TimeUnit.SECONDS)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            preferences.enabled = false
            stopSelf()
            return START_NOT_STICKY
        }
        scheduler.execute { heartbeatAndDrainSafe() }
        return START_STICKY
    }

    override fun onDestroy() {
        scheduler.shutdownNow()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun heartbeatAndDrainSafe() {
        if (!workInProgress.compareAndSet(false, true)) return
        val wakeLock = (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "$packageName:relay")
        try {
            wakeLock.acquire(45_000)
            if (!preferences.isReady()) {
                updateStatus("تنظیمات یا مجوزها کامل نیست")
                stopSelf()
                return
            }
            drainQueue()
            heartbeat()
        } catch (_: Exception) {
            updateStatus("ارتباط موقتاً قطع است؛ تلاش مجدد خودکار")
        } finally {
            if (wakeLock.isHeld) wakeLock.release()
            workInProgress.set(false)
        }
    }

    private fun drainQueue() {
        pendingStore.due().forEach { item ->
            val ageMillis = System.currentTimeMillis() - (item.receivedAtSeconds * 1000).toLong()
            if (ageMillis > OTP_MAX_AGE_MILLIS || item.attempts >= MAX_ATTEMPTS) {
                pendingStore.remove(item.messageId)
                return@forEach
            }
            val result = RelayApi.sendOtp(deviceId, item)
            when {
                result.success || result.code == 409 || result.code == 410 || result.code == 404 -> {
                    pendingStore.remove(item.messageId)
                    updateStatus(
                        if (result.success) "کد پیامک با موفقیت تحویل سرور شد"
                        else "سرور برای این کد ملی منتظر OTP نبود",
                    )
                }
                result.retryable -> {
                    val delay = min(300_000L, 10_000L * (1L shl min(item.attempts, 5)))
                    pendingStore.reschedule(item, delay)
                    updateStatus("ارسال OTP ناموفق؛ در صف تلاش مجدد")
                }
                else -> {
                    pendingStore.remove(item.messageId)
                    updateStatus("OTP رد شد: HTTP ${result.code}")
                }
            }
        }
    }

    private fun heartbeat() {
        val nationalIds = preferences.configuredNationalIds()
        if (nationalIds.isEmpty()) {
            updateStatus("کد ملی معتبر وارد نشده است")
            return
        }
        var reachable = false
        var waiting = false
        nationalIds.forEach { nationalId ->
            val result = RelayApi.checkStatus(deviceId, nationalId)
            if (result.success) {
                reachable = true
                waiting = waiting || result.waiting == true
            }
        }
        val queued = pendingStore.size()
        updateStatus(
            when {
                !reachable -> "سرور در دسترس نیست؛ تلاش مجدد خودکار"
                waiting -> "متصل؛ MahanBot منتظر پیامک است${queueSuffix(queued)}"
                else -> "متصل به سرور؛ منتظر پیامک${queueSuffix(queued)}"
            },
        )
    }

    private fun queueSuffix(size: Int): String = if (size > 0) " — $size مورد در صف" else ""

    private fun updateStatus(message: String) {
        preferences.lastStatus = message
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIFICATION_ID, notification(message))
    }

    private fun notification(message: String): Notification {
        val openIntent = Intent(this, MainActivity::class.java)
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(message)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .build()
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "نمایش وضعیت اتصال اپ، سرور و MahanBot"
            setShowBadge(false)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    companion object {
        const val ACTION_START = "ir.mahan.otprelay.START"
        const val ACTION_WAKE = "ir.mahan.otprelay.WAKE"
        const val ACTION_STOP = "ir.mahan.otprelay.STOP"
        private const val CHANNEL_ID = "mahan_otp_relay_connection"
        private const val NOTIFICATION_ID = 2801
        private const val HEARTBEAT_SECONDS = 20L
        private const val OTP_MAX_AGE_MILLIS = 30 * 60 * 1000L
        private const val MAX_ATTEMPTS = 10

        fun start(context: Context) {
            val intent = Intent(context, RelayForegroundService::class.java).setAction(ACTION_START)
            runCatching { context.startForegroundService(intent) }
        }

        fun wake(context: Context) {
            val intent = Intent(context, RelayForegroundService::class.java).setAction(ACTION_WAKE)
            runCatching { context.startForegroundService(intent) }
        }
    }
}
