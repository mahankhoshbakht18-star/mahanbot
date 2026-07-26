package ir.mahan.mahanbototp

import android.app.Activity
import com.google.android.gms.auth.api.phone.SmsRetriever

class ConsentLauncher {
    fun start(activity: Activity) {
        // Android owns the five-minute consent window and exposes one message only after user approval.
        SmsRetriever.getClient(activity).startSmsUserConsent(null)
    }
}
