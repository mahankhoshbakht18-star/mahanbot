package ir.mahan.mahanbototp

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalLayoutDirection
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
                    var studied by remember { mutableStateOf(false) }
                    var allowed by remember { mutableStateOf(false) }
                    Column(Modifier.fillMaxSize().padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                        Text("همراه OTP ماهان‌بات", style = MaterialTheme.typography.headlineMedium)
                        Card(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("وضعیت: جفت‌سازی نشده")
                                Text("فقط کد مرتبط با نشست فعال پردازش می‌شود؛ متن پیامک و فرستنده ارسال نمی‌شوند.")
                                Text("نسخه ${BuildConfig.APP_FLAVOR} — ۱.۰.۰")
                            }
                        }
                        ConsentLine("توضیحات حریم خصوصی را مطالعه کردم", studied) { studied = it }
                        ConsentLine("اجازه پردازش OTP نشست فعال را می‌دهم", allowed) { allowed = it }
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            Button(onClick = { }, enabled = studied && allowed) { Text("فعال‌سازی و جفت‌سازی") }
                            OutlinedButton(onClick = { studied = false; allowed = false }) { Text("لغو") }
                        }
                        Text("پیامک‌های قبلی خوانده نمی‌شوند. OTP پس از تأیید سرور حذف می‌شود و در Logcat چاپ نمی‌شود.")
                        Text("ورود دستی کد فقط برای نشست فعال در دسترس است. دستگاه را هر زمان می‌توانید Unpair کنید.")
                    }
                }
            }
        }
    }
}

@androidx.compose.runtime.Composable
private fun ConsentLine(text: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth()) { Checkbox(checked, onCheckedChange = onChange); Text(text, Modifier.padding(top = 12.dp)) }
}
