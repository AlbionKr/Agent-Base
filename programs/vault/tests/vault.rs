//! Security tests for the vault program. Run with `anchor test` (or
//! `anchor build && cargo test`). Each test names the guarantee it protects.

mod common;

use {
    common::*,
    solana_keypair::Keypair,
    solana_signer::Signer,
    vault::VaultError,
};

// ------------------------------------------------------------- happy path

#[test]
fn setup_funds_the_vault_and_records_roles() {
    let env = Env::new();
    let vault = env.vault_state();
    assert_eq!(vault.owner, env.owner.pubkey());
    assert_eq!(vault.guardian, env.guardian.pubkey());
    assert_eq!(vault.usdc_mint, env.mint);
    assert_eq!(vault.usdc_vault_ata, env.vault_ata);
    assert!(!vault.frozen);
    assert_eq!(env.balance(&env.vault_ata), DEPOSIT);
    assert_eq!(env.balance(&env.owner_ata), 0);

    let allowance = env.allowance_state();
    assert_eq!(allowance.vault, env.vault);
    assert_eq!(allowance.agent_session_key, env.agent.pubkey());
    assert_eq!(allowance.per_tx_cap, PER_TX_CAP);
    assert_eq!(allowance.daily_cap, DAILY_CAP);
    assert_eq!(allowance.allowlist, vec![env.payee.pubkey()]);
    assert_eq!(allowance.mandate_hash, [7u8; 32]);
    assert_eq!(allowance.spent_today, 0);
}

#[test]
fn top_up_within_caps_moves_funds_to_the_agent_wallet() {
    let mut env = Env::new();
    env.top_up(4 * USDC).expect("top_up");
    assert_eq!(env.balance(&env.agent_ata), 4 * USDC);
    assert_eq!(env.balance(&env.vault_ata), DEPOSIT - 4 * USDC);
    assert_eq!(env.allowance_state().spent_today, 4 * USDC);
}

#[test]
fn pay_to_an_allowlisted_payee_works() {
    let mut env = Env::new();
    env.pay(env.payee_ata, 3 * USDC).expect("pay");
    assert_eq!(env.balance(&env.payee_ata), 3 * USDC);
    assert_eq!(env.allowance_state().spent_today, 3 * USDC);
}

// ------------------------------------------------------------------- caps

#[test]
fn per_tx_cap_exceeded_reverts() {
    let mut env = Env::new();
    assert_vault_error(env.top_up(PER_TX_CAP + 1), VaultError::PerTxCapExceeded);
    assert_vault_error(
        env.pay(env.payee_ata, PER_TX_CAP + 1),
        VaultError::PerTxCapExceeded,
    );
    assert_eq!(env.balance(&env.vault_ata), DEPOSIT);
    assert_eq!(env.allowance_state().spent_today, 0);
    // Exactly at the cap is allowed.
    env.top_up(PER_TX_CAP).expect("top_up at cap");
}

#[test]
fn zero_amount_reverts() {
    let mut env = Env::new();
    assert_vault_error(env.top_up(0), VaultError::ZeroAmount);
}

#[test]
fn daily_cap_exceeded_reverts_and_is_shared_by_top_up_and_pay() {
    let mut env = Env::new();
    env.top_up(10 * USDC).expect("first");
    env.pay(env.payee_ata, 10 * USDC).expect("second");
    // 20 of 25 spent: 6 more would breach the daily cap, 5 fits exactly.
    assert_vault_error(env.top_up(6 * USDC), VaultError::DailyCapExceeded);
    assert_vault_error(env.pay(env.payee_ata, 6 * USDC), VaultError::DailyCapExceeded);
    env.top_up(5 * USDC).expect("exactly to the cap");
    assert_vault_error(env.top_up(1), VaultError::DailyCapExceeded);

    assert_eq!(env.allowance_state().spent_today, DAILY_CAP);
    assert_eq!(env.balance(&env.vault_ata), DEPOSIT - DAILY_CAP);
}

#[test]
fn daily_window_resets_after_24_hours() {
    let mut env = Env::new();
    env.top_up(10 * USDC).unwrap();
    env.top_up(10 * USDC).unwrap();
    env.top_up(5 * USDC).unwrap();
    assert_vault_error(env.top_up(USDC), VaultError::DailyCapExceeded);

    // One second before the window ends: still capped.
    env.advance(DAY - 1);
    assert_vault_error(env.top_up(USDC), VaultError::DailyCapExceeded);

    // Window over: the budget is fresh and a new window starts now.
    env.advance(1);
    env.top_up(10 * USDC).expect("new window");
    let allowance = env.allowance_state();
    assert_eq!(allowance.spent_today, 10 * USDC);
    assert_eq!(allowance.day_start, T0 + DAY);
}

#[test]
fn slow_drain_is_still_bounded_by_the_daily_cap() {
    // Many small transfers, each far under the per-tx cap.
    let mut env = Env::new();
    let mut moved = 0;
    for _ in 0..40 {
        if env.top_up(USDC).is_ok() {
            moved += USDC;
        }
    }
    assert_eq!(moved, DAILY_CAP);
    assert_eq!(env.balance(&env.vault_ata), DEPOSIT - DAILY_CAP);
}

// -------------------------------------------------------------- allowlist

#[test]
fn pay_to_a_non_allowlisted_destination_reverts() {
    let mut env = Env::new();
    assert_vault_error(
        env.pay(env.stranger_ata, USDC),
        VaultError::PayeeNotAllowlisted,
    );
    // The agent's own wallet is not a payee either: `pay` is allowlist-only.
    assert_vault_error(env.pay(env.agent_ata, USDC), VaultError::PayeeNotAllowlisted);
    assert_eq!(env.balance(&env.stranger_ata), 0);
    assert_eq!(env.allowance_state().spent_today, 0);
}

#[test]
fn top_up_cannot_be_redirected_to_someone_elses_token_account() {
    let mut env = Env::new();
    let agent = env.agent.insecure_clone();
    assert_rejected(env.top_up_with(&agent, env.allowance, env.stranger_ata, USDC));
    assert_rejected(env.top_up_with(&agent, env.allowance, env.payee_ata, USDC));
    assert_eq!(env.balance(&env.stranger_ata), 0);
    assert_eq!(env.balance(&env.vault_ata), DEPOSIT);
}

// ----------------------------------------------------------------- expiry

#[test]
fn expired_session_key_reverts() {
    let mut env = Env::new();
    let expires_at = env.allowance_state().expires_at;

    env.set_time(expires_at - 1);
    env.top_up(USDC).expect("still valid one second before expiry");

    env.set_time(expires_at);
    assert_vault_error(env.top_up(USDC), VaultError::SessionKeyExpired);
    assert_vault_error(env.pay(env.payee_ata, USDC), VaultError::SessionKeyExpired);
}

// ----------------------------------------------------------------- freeze

#[test]
fn frozen_agent_cannot_spend() {
    let mut env = Env::new();
    let guardian = env.guardian.insecure_clone();
    env.freeze_agent(&guardian).expect("guardian freezes");
    assert!(env.allowance_state().frozen);
    assert_vault_error(env.top_up(USDC), VaultError::AgentFrozen);
    assert_vault_error(env.pay(env.payee_ata, USDC), VaultError::AgentFrozen);
    assert_eq!(env.balance(&env.vault_ata), DEPOSIT);
}

#[test]
fn frozen_vault_stops_every_agent() {
    let mut env = Env::new();
    let second = Keypair::new();
    env.svm.airdrop(&second.pubkey(), 1_000_000_000).unwrap();
    let second_ata =
        anchor_spl::associated_token::get_associated_token_address(&second.pubkey(), &env.mint);
    env.put_token_account(second_ata, second.pubkey(), 0);
    let policy = env.default_policy();
    env.create_agent(2, second.pubkey(), policy).unwrap();
    let second_allowance = allowance_pda(&env.vault, 2);

    let guardian = env.guardian.insecure_clone();
    env.freeze_vault(&guardian).expect("guardian freezes vault");
    assert!(env.vault_state().frozen);

    assert_vault_error(env.top_up(USDC), VaultError::VaultFrozen);
    assert_vault_error(
        env.top_up_with(&second, second_allowance, second_ata, USDC),
        VaultError::VaultFrozen,
    );
}

#[test]
fn owner_can_also_freeze() {
    let mut env = Env::new();
    let owner = env.owner.insecure_clone();
    env.freeze_agent(&owner).expect("owner freezes agent");
    env.freeze_vault(&owner).expect("owner freezes vault");
    assert!(env.allowance_state().frozen && env.vault_state().frozen);
}

#[test]
fn guardian_can_never_unfreeze() {
    let mut env = Env::new();
    let guardian = env.guardian.insecure_clone();
    env.freeze_agent(&guardian).unwrap();
    env.freeze_vault(&guardian).unwrap();

    assert_rejected(env.unfreeze_agent(&guardian));
    assert_rejected(env.unfreeze_vault(&guardian));
    assert!(env.allowance_state().frozen, "agent must stay frozen");
    assert!(env.vault_state().frozen, "vault must stay frozen");
}

#[test]
fn only_the_owner_unfreezes_and_spending_resumes() {
    let mut env = Env::new();
    let guardian = env.guardian.insecure_clone();
    let owner = env.owner.insecure_clone();
    env.freeze_agent(&guardian).unwrap();
    env.freeze_vault(&guardian).unwrap();

    env.unfreeze_vault(&owner).expect("owner unfreezes vault");
    assert_vault_error(env.top_up(USDC), VaultError::AgentFrozen);
    env.unfreeze_agent(&owner).expect("owner unfreezes agent");
    env.top_up(USDC).expect("spending resumes");
}

#[test]
fn strangers_and_agents_cannot_freeze_or_unfreeze() {
    let mut env = Env::new();
    let stranger = env.stranger.insecure_clone();
    let agent = env.agent.insecure_clone();

    assert_vault_error(env.freeze_agent(&stranger), VaultError::Unauthorized);
    assert_vault_error(env.freeze_vault(&stranger), VaultError::Unauthorized);
    assert_vault_error(env.freeze_vault(&agent), VaultError::Unauthorized);
    assert!(!env.allowance_state().frozen && !env.vault_state().frozen);

    let guardian = env.guardian.insecure_clone();
    env.freeze_agent(&guardian).unwrap();
    assert_rejected(env.unfreeze_agent(&agent));
    assert_rejected(env.unfreeze_agent(&stranger));
    assert!(env.allowance_state().frozen);
}

// ------------------------------------------------------------ wrong signer

#[test]
fn only_the_session_key_can_spend_an_allowance() {
    let mut env = Env::new();
    let stranger = env.stranger.insecure_clone();
    let owner = env.owner.insecure_clone();
    let guardian = env.guardian.insecure_clone();

    assert_vault_error(
        env.top_up_with(&stranger, env.allowance, env.stranger_ata, USDC),
        VaultError::WrongSessionKey,
    );
    assert_vault_error(
        env.pay_with(&stranger, env.payee_ata, USDC),
        VaultError::WrongSessionKey,
    );
    // Not even the guardian or the owner can spend through an agent's allowance.
    assert_vault_error(
        env.pay_with(&guardian, env.payee_ata, USDC),
        VaultError::WrongSessionKey,
    );
    assert_vault_error(
        env.pay_with(&owner, env.payee_ata, USDC),
        VaultError::WrongSessionKey,
    );
    assert_eq!(env.balance(&env.vault_ata), DEPOSIT);
}

#[test]
fn one_agent_cannot_spend_another_agents_allowance() {
    let mut env = Env::new();
    let second = Keypair::new();
    env.svm.airdrop(&second.pubkey(), 1_000_000_000).unwrap();
    let second_ata =
        anchor_spl::associated_token::get_associated_token_address(&second.pubkey(), &env.mint);
    env.put_token_account(second_ata, second.pubkey(), 0);
    let policy = env.default_policy();
    env.create_agent(2, second.pubkey(), policy).unwrap();

    // Agent 2 signs but points at agent 1's allowance.
    assert_vault_error(
        env.top_up_with(&second, env.allowance, second_ata, USDC),
        VaultError::WrongSessionKey,
    );
    assert_eq!(env.allowance_state().spent_today, 0);
}

// ------------------------------------------------------- owner-only admin

#[test]
fn only_the_owner_can_create_agents() {
    let mut env = Env::new();
    let policy = env.default_policy();
    for intruder in [
        env.guardian.insecure_clone(),
        env.agent.insecure_clone(),
        env.stranger.insecure_clone(),
    ] {
        assert_rejected(env.create_agent_as(&intruder, 9, intruder.pubkey(), policy.clone()));
    }
    assert!(env.svm.get_account(&allowance_pda(&env.vault, 9)).is_none());
}

#[test]
fn only_the_owner_can_update_policy() {
    let mut env = Env::new();
    let mut generous = env.default_policy();
    generous.per_tx_cap = 500 * USDC;
    generous.daily_cap = 500 * USDC;
    generous.allowlist = vec![env.stranger.pubkey()];

    for intruder in [
        env.guardian.insecure_clone(),
        env.agent.insecure_clone(),
        env.stranger.insecure_clone(),
    ] {
        assert_rejected(env.update_policy(&intruder, generous.clone()));
    }
    let unchanged = env.allowance_state();
    assert_eq!(unchanged.per_tx_cap, PER_TX_CAP);
    assert_eq!(unchanged.daily_cap, DAILY_CAP);
    assert_eq!(unchanged.allowlist, vec![env.payee.pubkey()]);

    let owner = env.owner.insecure_clone();
    env.update_policy(&owner, generous).expect("owner updates");
    let updated = env.allowance_state();
    assert_eq!(updated.per_tx_cap, 500 * USDC);
    assert_eq!(updated.allowlist, vec![env.stranger.pubkey()]);
    // The new allowlist is what `pay` enforces from now on.
    env.pay(env.stranger_ata, 100 * USDC).expect("new payee");
    assert_vault_error(env.pay(env.payee_ata, USDC), VaultError::PayeeNotAllowlisted);
}

#[test]
fn policy_validation_rejects_bad_parameters() {
    let mut env = Env::new();
    let owner = env.owner.insecure_clone();

    let mut too_many = env.default_policy();
    too_many.allowlist = (0..9).map(|_| Keypair::new().pubkey()).collect();
    assert_vault_error(env.update_policy(&owner, too_many), VaultError::AllowlistTooLong);

    let mut inverted = env.default_policy();
    inverted.per_tx_cap = inverted.daily_cap + 1;
    assert_vault_error(env.update_policy(&owner, inverted), VaultError::InvalidCaps);

    let mut expired = env.default_policy();
    expired.expires_at = T0;
    assert_vault_error(env.update_policy(&owner, expired), VaultError::InvalidExpiry);

    let mut full = env.default_policy();
    full.allowlist = (0..8).map(|_| Keypair::new().pubkey()).collect();
    env.update_policy(&owner, full).expect("8 payees is the maximum and fits");
}

#[test]
fn only_the_owner_can_rotate_the_session_key() {
    let mut env = Env::new();
    let new_key = Keypair::new();
    env.svm.airdrop(&new_key.pubkey(), 1_000_000_000).unwrap();
    let new_ata =
        anchor_spl::associated_token::get_associated_token_address(&new_key.pubkey(), &env.mint);
    env.put_token_account(new_ata, new_key.pubkey(), 0);

    for intruder in [
        env.guardian.insecure_clone(),
        env.agent.insecure_clone(),
        env.stranger.insecure_clone(),
    ] {
        assert_rejected(env.rotate_session_key(&intruder, intruder.pubkey(), T0 + 30 * DAY));
    }
    assert_eq!(env.allowance_state().agent_session_key, env.agent.pubkey());

    let owner = env.owner.insecure_clone();
    env.rotate_session_key(&owner, new_key.pubkey(), T0 + 30 * DAY)
        .expect("owner rotates");

    // Old key is dead immediately; the new key works.
    assert_vault_error(env.top_up(USDC), VaultError::WrongSessionKey);
    env.top_up_with(&new_key, env.allowance, new_ata, USDC)
        .expect("new key spends");
    assert_eq!(env.balance(&new_ata), USDC);
}

#[test]
fn rotation_does_not_reset_the_daily_budget() {
    let mut env = Env::new();
    env.top_up(10 * USDC).unwrap();
    env.top_up(10 * USDC).unwrap();

    let new_key = Keypair::new();
    env.svm.airdrop(&new_key.pubkey(), 1_000_000_000).unwrap();
    let new_ata =
        anchor_spl::associated_token::get_associated_token_address(&new_key.pubkey(), &env.mint);
    env.put_token_account(new_ata, new_key.pubkey(), 0);
    let owner = env.owner.insecure_clone();
    env.rotate_session_key(&owner, new_key.pubkey(), T0 + 30 * DAY)
        .unwrap();

    assert_vault_error(
        env.top_up_with(&new_key, env.allowance, new_ata, 6 * USDC),
        VaultError::DailyCapExceeded,
    );
}

#[test]
fn only_the_owner_can_withdraw_even_while_frozen() {
    let mut env = Env::new();
    let guardian = env.guardian.insecure_clone();
    let agent = env.agent.insecure_clone();
    let stranger = env.stranger.insecure_clone();
    let owner = env.owner.insecure_clone();

    assert_rejected(env.withdraw(&guardian, env.owner_ata, USDC));
    assert_rejected(env.withdraw(&agent, env.agent_ata, USDC));
    assert_rejected(env.withdraw(&stranger, env.stranger_ata, USDC));
    assert_eq!(env.balance(&env.vault_ata), DEPOSIT);

    env.freeze_vault(&guardian).unwrap();
    env.withdraw(&owner, env.owner_ata, DEPOSIT)
        .expect("owner recovers funds while frozen");
    assert_eq!(env.balance(&env.owner_ata), DEPOSIT);
    assert_eq!(env.balance(&env.vault_ata), 0);
}

#[test]
fn creating_an_agent_validates_its_policy() {
    let mut env = Env::new();
    let mut bad = env.default_policy();
    bad.expires_at = T0 - 1;
    assert_vault_error(
        env.create_agent(5, Keypair::new().pubkey(), bad),
        VaultError::InvalidExpiry,
    );
}
