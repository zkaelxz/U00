// Mirrors the member-key models in api/schemas/extension_access.py. Never the key.

export interface MemberKeyStatus {
  has_key: boolean
  // The key's last four characters, the most any response shows.
  last4: string | null
  // Epoch seconds.
  added_at: number | null
  // A key is saved but this server can't read it any more: paste it again.
  unreadable: boolean
}

export interface MemberKeyRemoved {
  user_id: number
  removed: boolean
}
