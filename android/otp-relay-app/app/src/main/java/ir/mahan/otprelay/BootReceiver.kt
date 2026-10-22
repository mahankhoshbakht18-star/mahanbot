package ir.mahan.otprelay

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val preferences = RelayPreferences(context)
        if (preferences.isReady()) {
            RelayForegroundService.start(context)
        }
    }
}
