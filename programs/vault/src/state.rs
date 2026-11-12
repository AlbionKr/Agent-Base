use anchor_lang::prelude::*;

pub const VAULT_SEED: &[u8] = b"vault";
pub const AGENT_SEED: &[u8] = b"agent";
pub const MAX_ALLOWLIST: usize = 8;
pub const DAY_SECONDS: i64 = 86_400;

/// One vault per owner. Holds the USDC that agents draw from.
#[account]
#[derive(InitSpace)]
pub struct Vault {
    pub owner: Pubkey,
    /// Backend key that may freeze. It can never unfreeze, spend, or change policy.
    pub guardian: Pubkey,
    pub usdc_mint: Pubkey,
    pub usdc_vault_ata: Pubkey,
    pub frozen: bool,
    pub bump: u8,
}

/// Spending limits for one agent. The session key is also the agent's hot wallet.
#[account]
#[derive(InitSpace)]
pub struct AgentAllowance {
    pub vault: Pubkey,
    pub agent_id: u64,
    pub agent_session_key: Pubkey,
    pub expires_at: i64,
    pub per_tx_cap: u64,
    pub daily_cap: u64,
    pub spent_today: u64,
    /// Start of the current 24h spending window (unix seconds).
    pub day_start: i64,
    /// Wallets this agent may `pay`. `top_up` always goes to the session key itself.
    #[max_len(MAX_ALLOWLIST)]
    pub allowlist: Vec<Pubkey>,
    pub frozen: bool,
    /// sha256 of the user-signed mandate the offchain policy was compiled from.
    pub mandate_hash: [u8; 32],
    pub bump: u8,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Debug)]
pub struct AgentPolicy {
    pub expires_at: i64,
    pub per_tx_cap: u64,
    pub daily_cap: u64,
    pub allowlist: Vec<Pubkey>,
    pub mandate_hash: [u8; 32],
}

impl AgentAllowance {
    pub fn apply_policy(&mut self, policy: &AgentPolicy, now: i64) -> Result<()> {
        require!(
            policy.allowlist.len() <= MAX_ALLOWLIST,
            crate::VaultError::AllowlistTooLong
        );
        require!(policy.expires_at > now, crate::VaultError::InvalidExpiry);
        require!(
            policy.per_tx_cap <= policy.daily_cap,
            crate::VaultError::InvalidCaps
        );
        self.expires_at = policy.expires_at;
        self.per_tx_cap = policy.per_tx_cap;
        self.daily_cap = policy.daily_cap;
        self.allowlist = policy.allowlist.clone();
        self.mandate_hash = policy.mandate_hash;
        Ok(())
    }

    /// The single gate every agent-initiated outflow passes through.
    /// Checks freeze, expiry, per-tx cap and the rolling daily cap, then records the spend.
    pub fn authorize_spend(&mut self, vault_frozen: bool, amount: u64, now: i64) -> Result<()> {
        require!(!vault_frozen, crate::VaultError::VaultFrozen);
        require!(!self.frozen, crate::VaultError::AgentFrozen);
        require!(now < self.expires_at, crate::VaultError::SessionKeyExpired);
        require!(amount > 0, crate::VaultError::ZeroAmount);
        require!(amount <= self.per_tx_cap, crate::VaultError::PerTxCapExceeded);

        if now >= self.day_start.saturating_add(DAY_SECONDS) {
            self.day_start = now;
            self.spent_today = 0;
        }
        let spent = self
            .spent_today
            .checked_add(amount)
            .ok_or(crate::VaultError::MathOverflow)?;
        require!(spent <= self.daily_cap, crate::VaultError::DailyCapExceeded);
        self.spent_today = spent;
        Ok(())
    }
}
