package ir.mahan.mahanbototp

import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Index
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.RoomDatabase
import androidx.room.Transaction

@Entity(tableName = "pending_otp", indices = [Index(value = ["messageId"], unique = true)])
data class PendingOtpEntity(
    @androidx.room.PrimaryKey val messageId: String,
    val sessionId: String,
    val encryptedOtp: String,
    val nationalIdHash: String,
    val receivedAt: Long,
    val simSlot: Int?,
    val attemptCount: Int = 0,
    val nextAttemptAt: Long,
    val createdAt: Long,
    val expiresAt: Long,
    val status: String = "QUEUED",
    val lastErrorCategory: String? = null,
)

@Dao
interface PendingOtpDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun enqueue(item: PendingOtpEntity): Long
    @Query("SELECT * FROM pending_otp WHERE status IN ('QUEUED','RETRY') AND nextAttemptAt <= :now ORDER BY createdAt LIMIT :limit")
    suspend fun findDue(now: Long, limit: Int = 20): List<PendingOtpEntity>
    @Query("UPDATE pending_otp SET status='SENDING' WHERE messageId=:id AND status IN ('QUEUED','RETRY')") suspend fun markSending(id: String): Int
    @Query("UPDATE pending_otp SET status='RETRY', attemptCount=attemptCount+1, nextAttemptAt=:nextAt, lastErrorCategory=:category WHERE messageId=:id")
    suspend fun reschedule(id: String, nextAt: Long, category: String)
    @Query("DELETE FROM pending_otp WHERE messageId=:id") suspend fun acknowledge(id: String)
    @Query("DELETE FROM pending_otp WHERE messageId=:id") suspend fun remove(id: String)
    @Query("DELETE FROM pending_otp WHERE expiresAt <= :now") suspend fun purgeExpired(now: Long): Int
    @Query("SELECT COUNT(*) FROM pending_otp") suspend fun countPending(): Int
    @Query("SELECT EXISTS(SELECT 1 FROM pending_otp WHERE messageId=:id)") suspend fun containsMessageId(id: String): Boolean
    @Query("DELETE FROM pending_otp WHERE messageId IN (SELECT messageId FROM pending_otp ORDER BY createdAt ASC LIMIT :count)") suspend fun trimOldest(count: Int)

    @Transaction suspend fun enqueueBounded(item: PendingOtpEntity, maximum: Int = 100): Boolean {
        val excess = countPending() - maximum + 1
        if (excess > 0) trimOldest(excess)
        return enqueue(item) != -1L
    }
}

@Database(entities = [PendingOtpEntity::class], version = 1, exportSchema = true)
abstract class OtpDatabase : RoomDatabase() { abstract fun pendingOtpDao(): PendingOtpDao }
