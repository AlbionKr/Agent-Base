//! Agent Base vault.
//!
//! The last line of defense: even if the agent and the offchain policy engine are
//! both compromised, an agent's session key can move at most `per_tx_cap` per
//! transaction and `daily_cap` per 24h, only to itself (`top_up`) or to an
//! allowlisted payee (`pay`), only until `expires_at`, and only while not frozen.
//!
//! Roles
//! - owner:    full control. The only key that can unfreeze, change policy,
//!             rotate session keys, or withdraw.
//! - guardian: backend key. Can freeze an agent or the vault. Nothing else.
//! - agent:    session key. Can `top_up` and `pay` within its allowance.

use anchor_lang::prelude::*;
use anchor_spl::associated_token::AssociatedToken;
use anchor_spl::token::{self, Mint, Token, TokenAccount, TransferChecked};

pub mod state;
pub use state::*;

declare_id!("8en8vCL4DR8e3Y739FSgKtLzFvTwiMvD8GVgnKGTBV2x");

#[program]
pub mod vault {
    use super::*;

    pub fn initialize_vault(ctx: Context<InitializeVault>, guardian: Pubkey) -> Result<()> {
        let vault = &mut ctx.accounts.vault;
        vault.owner = ctx.accounts.owner.key();
        vault.guardian = guardian;
        vault.usdc_mint = ctx.accounts.usdc_mint.key();
        vault.usdc_vault_ata = ctx.accounts.usdc_vault_ata.key();
        vault.frozen = false;
        vault.bump = ctx.bumps.vault;
        Ok(())
    }

    /// Anyone may add funds. Only the rules below let funds leave.
    pub fn deposit(ctx: Context<Deposit>, amount: u64) -> Result<()> {
        require!(amount > 0, VaultError::ZeroAmount);
        token::transfer_checked(
            CpiContext::new(
                ctx.accounts.token_program.key(),
                TransferChecked {
                    from: ctx.accounts.depositor_token_account.to_account_info(),
                    mint: ctx.accounts.usdc_mint.to_account_info(),
                    to: ctx.accounts.usdc_vault_ata.to_account_info(),
                    authority: ctx.accounts.depositor.to_account_info(),
                },
            ),
            amount,
            ctx.accounts.usdc_mint.decimals,
        )
    }

    pub fn create_agent(
        ctx: Context<CreateAgent>,
        agent_id: u64,
        session_key: Pubkey,
        policy: AgentPolicy,
    ) -> Result<()> {
        let now = Clock::get()?.unix_timestamp;
        let allowance = &mut ctx.accounts.allowance;
        allowance.vault = ctx.accounts.vault.key();
        allowance.agent_id = agent_id;
        allowance.agent_session_key = session_key;
        allowance.spent_today = 0;
        allowance.day_start = now;
        allowance.frozen = false;
        allowance.bump = ctx.bumps.allowance;
        allowance.apply_policy(&policy, now)
    }

    /// Agent moves USDC from the vault to its own hot wallet, within its caps.
    pub fn top_up(ctx: Context<TopUp>, amount: u64) -> Result<()> {
        let now = Clock::get()?.unix_timestamp;
        let vault_frozen = ctx.accounts.vault.frozen;
        ctx.accounts
            .allowance
            .authorize_spend(vault_frozen, amount, now)?;

        transfer_from_vault(
            &ctx.accounts.vault,
            &ctx.accounts.usdc_vault_ata,
            &ctx.accounts.agent_token_account,
            &ctx.accounts.usdc_mint,
            &ctx.accounts.token_program,
            amount,
        )?;
        emit!(Spend {
            agent_id: ctx.accounts.allowance.agent_id,
            kind: SpendKind::TopUp,
            amount,
            destination: ctx.accounts.agent_token_account.owner,
            spent_today: ctx.accounts.allowance.spent_today,
        });
        Ok(())
    }

    /// Agent pays an allowlisted payee directly from the vault, within its caps.
    pub fn pay(ctx: Context<Pay>, amount: u64) -> Result<()> {
        let payee = ctx.accounts.payee_token_account.owner;
        require!(
            ctx.accounts.allowance.allowlist.contains(&payee),
            VaultError::PayeeNotAllowlisted
        );
        let now = Clock::get()?.unix_timestamp;
        let vault_frozen = ctx.accounts.vault.frozen;
        ctx.accounts
            .allowance
            .authorize_spend(vault_frozen, amount, now)?;

        transfer_from_vault(
            &ctx.accounts.vault,
            &ctx.accounts.usdc_vault_ata,
            &ctx.accounts.payee_token_account,
            &ctx.accounts.usdc_mint,
            &ctx.accounts.token_program,
            amount,
        )?;
        emit!(Spend {
            agent_id: ctx.accounts.allowance.agent_id,
            kind: SpendKind::Pay,
            amount,
            destination: payee,
            spent_today: ctx.accounts.allowance.spent_today,
        });
        Ok(())
    }

    /// Owner or guardian.
    pub fn freeze_agent(ctx: Context<FreezeAgent>) -> Result<()> {
        ctx.accounts.allowance.frozen = true;
        emit!(Frozen {
            by: ctx.accounts.authority.key(),
            agent_id: Some(ctx.accounts.allowance.agent_id),
        });
        Ok(())
    }

    /// Owner or guardian. Stops every agent of this vault.
    pub fn freeze_vault(ctx: Context<FreezeVault>) -> Result<()> {
        ctx.accounts.vault.frozen = true;
        emit!(Frozen {
            by: ctx.accounts.authority.key(),
            agent_id: None,
        });
        Ok(())
    }

    /// Owner only. The guardian can never undo a freeze.
    pub fn unfreeze_agent(ctx: Context<OwnerAgentAction>) -> Result<()> {
        ctx.accounts.allowance.frozen = false;
        Ok(())
    }

    /// Owner only.
    pub fn unfreeze_vault(ctx: Context<OwnerVaultAction>) -> Result<()> {
        ctx.accounts.vault.frozen = false;
        Ok(())
    }

    /// Owner only. Replaces caps, expiry, allowlist and mandate hash.
    pub fn update_policy(ctx: Context<OwnerAgentAction>, policy: AgentPolicy) -> Result<()> {
        let now = Clock::get()?.unix_timestamp;
        ctx.accounts.allowance.apply_policy(&policy, now)
    }

    /// Owner only. The old key stops working immediately.
    pub fn rotate_session_key(
        ctx: Context<OwnerAgentAction>,
        new_session_key: Pubkey,
        new_expires_at: i64,
    ) -> Result<()> {
        let now = Clock::get()?.unix_timestamp;
        require!(new_expires_at > now, VaultError::InvalidExpiry);
        let allowance = &mut ctx.accounts.allowance;
        allowance.agent_session_key = new_session_key;
        allowance.expires_at = new_expires_at;
        Ok(())
    }

    /// Owner only. Works even while frozen so the owner can always recover funds.
    pub fn withdraw(ctx: Context<Withdraw>, amount: u64) -> Result<()> {
        require!(amount > 0, VaultError::ZeroAmount);
        transfer_from_vault(
            &ctx.accounts.vault,
            &ctx.accounts.usdc_vault_ata,
            &ctx.accounts.owner_token_account,
            &ctx.accounts.usdc_mint,
            &ctx.accounts.token_program,
            amount,
        )
    }
}

fn transfer_from_vault<'info>(
    vault: &Account<'info, Vault>,
    from: &Account<'info, TokenAccount>,
    to: &Account<'info, TokenAccount>,
    mint: &Account<'info, Mint>,
    token_program: &Program<'info, Token>,
    amount: u64,
) -> Result<()> {
    let seeds: &[&[u8]] = &[VAULT_SEED, vault.owner.as_ref(), &[vault.bump]];
    token::transfer_checked(
        CpiContext::new_with_signer(
            token_program.key(),
            TransferChecked {
                from: from.to_account_info(),
                mint: mint.to_account_info(),
                to: to.to_account_info(),
                authority: vault.to_account_info(),
            },
            &[seeds],
        ),
        amount,
        mint.decimals,
    )
}

// ---------------------------------------------------------------- accounts

#[derive(Accounts)]
pub struct InitializeVault<'info> {
    #[account(mut)]
    pub owner: Signer<'info>,
    #[account(
        init,
        payer = owner,
        space = 8 + Vault::INIT_SPACE,
        seeds = [VAULT_SEED, owner.key().as_ref()],
        bump
    )]
    pub vault: Account<'info, Vault>,
    pub usdc_mint: Account<'info, Mint>,
    #[account(
        init,
        payer = owner,
        associated_token::mint = usdc_mint,
        associated_token::authority = vault
    )]
    pub usdc_vault_ata: Account<'info, TokenAccount>,
    pub token_program: Program<'info, Token>,
    pub associated_token_program: Program<'info, AssociatedToken>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct Deposit<'info> {
    pub depositor: Signer<'info>,
    #[account(seeds = [VAULT_SEED, vault.owner.as_ref()], bump = vault.bump)]
    pub vault: Account<'info, Vault>,
    #[account(mut, token::mint = usdc_mint, token::authority = depositor)]
    pub depositor_token_account: Account<'info, TokenAccount>,
    #[account(mut, address = vault.usdc_vault_ata)]
    pub usdc_vault_ata: Account<'info, TokenAccount>,
    #[account(address = vault.usdc_mint)]
    pub usdc_mint: Account<'info, Mint>,
    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
#[instruction(agent_id: u64)]
pub struct CreateAgent<'info> {
    #[account(mut)]
    pub owner: Signer<'info>,
    #[account(
        seeds = [VAULT_SEED, owner.key().as_ref()],
        bump = vault.bump,
        has_one = owner @ VaultError::Unauthorized
    )]
    pub vault: Account<'info, Vault>,
    #[account(
        init,
        payer = owner,
        space = 8 + AgentAllowance::INIT_SPACE,
        seeds = [AGENT_SEED, vault.key().as_ref(), &agent_id.to_le_bytes()],
        bump
    )]
    pub allowance: Account<'info, AgentAllowance>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct TopUp<'info> {
    pub agent: Signer<'info>,
    #[account(seeds = [VAULT_SEED, vault.owner.as_ref()], bump = vault.bump)]
    pub vault: Account<'info, Vault>,
    #[account(
        mut,
        seeds = [AGENT_SEED, vault.key().as_ref(), &allowance.agent_id.to_le_bytes()],
        bump = allowance.bump,
        has_one = vault,
        constraint = allowance.agent_session_key == agent.key() @ VaultError::WrongSessionKey
    )]
    pub allowance: Account<'info, AgentAllowance>,
    #[account(mut, address = vault.usdc_vault_ata)]
    pub usdc_vault_ata: Account<'info, TokenAccount>,
    /// Must be owned by the session key: a top-up can only land in the agent's own wallet.
    #[account(mut, token::mint = usdc_mint, token::authority = agent)]
    pub agent_token_account: Account<'info, TokenAccount>,
    #[account(address = vault.usdc_mint)]
    pub usdc_mint: Account<'info, Mint>,
    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct Pay<'info> {
    pub agent: Signer<'info>,
    #[account(seeds = [VAULT_SEED, vault.owner.as_ref()], bump = vault.bump)]
    pub vault: Account<'info, Vault>,
    #[account(
        mut,
        seeds = [AGENT_SEED, vault.key().as_ref(), &allowance.agent_id.to_le_bytes()],
        bump = allowance.bump,
        has_one = vault,
        constraint = allowance.agent_session_key == agent.key() @ VaultError::WrongSessionKey
    )]
    pub allowance: Account<'info, AgentAllowance>,
    #[account(mut, address = vault.usdc_vault_ata)]
    pub usdc_vault_ata: Account<'info, TokenAccount>,
    /// The token account's owner must be on the agent's allowlist (checked in the handler).
    #[account(mut, token::mint = usdc_mint)]
    pub payee_token_account: Account<'info, TokenAccount>,
    #[account(address = vault.usdc_mint)]
    pub usdc_mint: Account<'info, Mint>,
    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct FreezeAgent<'info> {
    pub authority: Signer<'info>,
    #[account(
        seeds = [VAULT_SEED, vault.owner.as_ref()],
        bump = vault.bump,
        constraint = (authority.key() == vault.owner || authority.key() == vault.guardian)
            @ VaultError::Unauthorized
    )]
    pub vault: Account<'info, Vault>,
    #[account(
        mut,
        seeds = [AGENT_SEED, vault.key().as_ref(), &allowance.agent_id.to_le_bytes()],
        bump = allowance.bump,
        has_one = vault
    )]
    pub allowance: Account<'info, AgentAllowance>,
}

#[derive(Accounts)]
pub struct FreezeVault<'info> {
    pub authority: Signer<'info>,
    #[account(
        mut,
        seeds = [VAULT_SEED, vault.owner.as_ref()],
        bump = vault.bump,
        constraint = (authority.key() == vault.owner || authority.key() == vault.guardian)
            @ VaultError::Unauthorized
    )]
    pub vault: Account<'info, Vault>,
}

/// Owner-only actions on one agent: unfreeze, update policy, rotate session key.
#[derive(Accounts)]
pub struct OwnerAgentAction<'info> {
    pub owner: Signer<'info>,
    #[account(
        seeds = [VAULT_SEED, owner.key().as_ref()],
        bump = vault.bump,
        has_one = owner @ VaultError::Unauthorized
    )]
    pub vault: Account<'info, Vault>,
    #[account(
        mut,
        seeds = [AGENT_SEED, vault.key().as_ref(), &allowance.agent_id.to_le_bytes()],
        bump = allowance.bump,
        has_one = vault
    )]
    pub allowance: Account<'info, AgentAllowance>,
}

#[derive(Accounts)]
pub struct OwnerVaultAction<'info> {
    pub owner: Signer<'info>,
    #[account(
        mut,
        seeds = [VAULT_SEED, owner.key().as_ref()],
        bump = vault.bump,
        has_one = owner @ VaultError::Unauthorized
    )]
    pub vault: Account<'info, Vault>,
}

#[derive(Accounts)]
pub struct Withdraw<'info> {
    pub owner: Signer<'info>,
    #[account(
        seeds = [VAULT_SEED, owner.key().as_ref()],
        bump = vault.bump,
        has_one = owner @ VaultError::Unauthorized
    )]
    pub vault: Account<'info, Vault>,
    #[account(mut, address = vault.usdc_vault_ata)]
    pub usdc_vault_ata: Account<'info, TokenAccount>,
    #[account(mut, token::mint = usdc_mint, token::authority = owner)]
    pub owner_token_account: Account<'info, TokenAccount>,
    #[account(address = vault.usdc_mint)]
    pub usdc_mint: Account<'info, Mint>,
    pub token_program: Program<'info, Token>,
}

// ------------------------------------------------------------------ events

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, Debug, PartialEq, Eq)]
pub enum SpendKind {
    TopUp,
    Pay,
}

#[event]
pub struct Spend {
    pub agent_id: u64,
    pub kind: SpendKind,
    pub amount: u64,
    pub destination: Pubkey,
    pub spent_today: u64,
}

#[event]
pub struct Frozen {
    pub by: Pubkey,
    /// `None` means the whole vault was frozen.
    pub agent_id: Option<u64>,
}

// ------------------------------------------------------------------ errors

#[error_code]
pub enum VaultError {
    #[msg("Signer is not allowed to perform this action")]
    Unauthorized,
    #[msg("Signer is not this agent's session key")]
    WrongSessionKey,
    #[msg("Vault is frozen")]
    VaultFrozen,
    #[msg("Agent is frozen")]
    AgentFrozen,
    #[msg("Agent session key has expired")]
    SessionKeyExpired,
    #[msg("Amount exceeds the per-transaction cap")]
    PerTxCapExceeded,
    #[msg("Amount exceeds the remaining daily cap")]
    DailyCapExceeded,
    #[msg("Payee is not on the agent's allowlist")]
    PayeeNotAllowlisted,
    #[msg("Allowlist holds at most 8 payees")]
    AllowlistTooLong,
    #[msg("Expiry must be in the future")]
    InvalidExpiry,
    #[msg("Per-transaction cap cannot exceed the daily cap")]
    InvalidCaps,
    #[msg("Amount must be greater than zero")]
    ZeroAmount,
    #[msg("Arithmetic overflow")]
    MathOverflow,
}
