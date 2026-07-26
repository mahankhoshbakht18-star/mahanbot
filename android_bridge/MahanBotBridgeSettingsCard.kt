package ir.mahanvip.smsforwardmanager.mahanbot

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp

@Composable
fun MahanBotBridgeSettingsCard(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val initial = remember { MahanBotBridgeSettings.load(context) }
    var enabled by remember { mutableStateOf(initial.enabled) }
    var baseUrl by remember { mutableStateOf(initial.baseUrl) }
    var deviceKey by remember { mutableStateOf(initial.deviceKey) }
    var statusText by remember { mutableStateOf("") }
    var testing by remember { mutableStateOf(false) }

    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text("اتصال اعلان پیامک به MahanBot", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "فقط رسیدن پیامک اعلام می‌شود؛ متن و کد تأیید منتقل نمی‌شود.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Switch(checked = enabled, onCheckedChange = { enabled = it })
            }

            OutlinedTextField(
                value = baseUrl,
                onValueChange = { baseUrl = it },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                label = { Text("نشانی MahanBot") },
                supportingText = { Text("مثال: http://192.168.1.20:8000") },
            )

            OutlinedTextField(
                value = deviceKey,
                onValueChange = { deviceKey = it },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                label = { Text("کلید اتصال دستگاه") },
                visualTransformation = PasswordVisualTransformation(),
            )

            Text(
                "شناسه دستگاه: ${initial.deviceId}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = {
                        val saved = MahanBotBridgeConfig(
                            enabled = enabled,
                            baseUrl = baseUrl,
                            deviceId = initial.deviceId,
                            deviceKey = deviceKey,
                        )
                        MahanBotBridgeSettings.save(context, saved)
                        statusText = if (saved.isUsable()) "تنظیمات ذخیره شد." else "تنظیمات ذخیره شد، اما هنوز کامل نیست."
                    },
                ) {
                    Text("ذخیره")
                }

                Button(
                    enabled = !testing,
                    onClick = {
                        val saved = MahanBotBridgeConfig(
                            enabled = enabled,
                            baseUrl = baseUrl,
                            deviceId = initial.deviceId,
                            deviceKey = deviceKey,
                        )
                        MahanBotBridgeSettings.save(context, saved)
                        testing = true
                        statusText = "در حال بررسی اتصال…"
                        MahanBotNotifyClient.heartbeat(context) { result ->
                            statusText = when (result) {
                                is MahanBotNotifyResult.Success -> "اتصال با MahanBot برقرار است."
                                is MahanBotNotifyResult.Skipped -> "اتصال فعال یا کامل نیست."
                                is MahanBotNotifyResult.Failure -> "خطای اتصال: ${result.message}"
                            }
                            testing = false
                        }
                    },
                ) {
                    if (testing) {
                        CircularProgressIndicator(strokeWidth = 2.dp)
                    } else {
                        Text("تست اتصال")
                    }
                }
            }

            if (statusText.isNotBlank()) {
                Text(statusText, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}
