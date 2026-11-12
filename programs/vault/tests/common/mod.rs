//! Shared LiteSVM harness for the vault tests.
//!
//! The compiled program (`target/deploy/vault.so`) runs inside an in-process
//! Solana VM with the real SPL Token and Associated Token programs, so every
//! test exercises the same bytecode that is deployed to devnet. The clock is
//! controllable, which is what lets us test expiry and the 24h window.
#![allow(dead_code)]

use {
    anchor_lang::{
        prelude::{Clock, Pubkey},
        solana_program::{instruction::Instruction, system_program},
        AccountDeserialize, InstructionData, ToAccountMetas,
    },
    anchor_spl::associated_token::get_associated_token_address,
    litesvm::{
        types::{FailedTransactionMetadata, TransactionMetadata},
        LiteSVM,
    },
    solana_account::Account,
    solana_keypair::Keypair,
    solana_message::{Message, VersionedMessage},
    solana_signer::Signer,
    solana_transaction::versioned::VersionedTransaction,
    vault::{AgentAllowance, AgentPolicy, Vault, VaultError, AGENT_SEED, VAULT_SEED},
};

pub const USDC: u64 = 1_000_000; // 6 decimals
pub const T0: i64 = 1_800_000_000;
pub const DAY: i64 = 86_400;
pub const AGENT_ID: u64 = 1;

pub const PER_TX_CAP: u64 = 10 * USDC;
pub const DAILY_CAP: u64 = 25 * USDC;
pub const DEPOSIT: u64 = 1_000 * USDC;

pub type TxResult = Result<TransactionMetadata, FailedTransactionMetadata>;

pub struct Env {
    pub svm: LiteSVM,
    pub owner: Keypair,
    pub guardian: Keypair,
    pub agent: Keypair,
    pub payee: Keypair,
    pub stranger: Keypair,
    pub mint: Pubkey,
    pub vault: Pubkey,
    pub vault_ata: Pubkey,
    pub allowance: Pubkey,
    pub owner_ata: Pubkey,
    pub agent_ata: Pubkey,
    pub payee_ata: Pubkey,
    pub stranger_ata: Pubkey,
}

pub fn vault_pda(owner: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(&[VAULT_SEED, owner.as_ref()], &vault::id()).0
}

pub fn allowance_pda(vault: &Pubkey, agent_id: u64) -> Pubkey {
    Pubkey::find_program_address(
        &[AGENT_SEED, vault.as_ref(), &agent_id.to_le_bytes()],
        &vault::id(),
    )
    .0
}

fn mint_bytes(decimals: u8) -> Vec<u8> {
    // SPL Token Mint layout (82 bytes): no mint authority, supply 0, initialized.
    let mut data = vec![0u8; 82];
    data[44] = decimals;
    data[45] = 1;
    data
}

fn token_account_bytes(mint: &Pubkey, owner: &Pubkey, amount: u64) -> Vec<u8> {
    // SPL Token Account layout (165 bytes).
    let mut data = vec![0u8; 165];
    data[0..32].copy_from_slice(mint.as_ref());
    data[32..64].copy_from_slice(owner.as_ref());
    data[64..72].copy_from_slice(&amount.to_le_bytes());
    data[108] = 1; // AccountState::Initialized
    data
}

impl Env {
    /// A funded vault with one agent: per-tx cap 10 USDC, daily cap 25 USDC,
    /// key valid for 7 days, allowlist = [payee].
    pub fn new() -> Self {
        let mut svm = LiteSVM::new();
        let bytes = include_bytes!(concat!(env!("CARGO_TARGET_TMPDIR"), "/../deploy/vault.so"));
        svm.add_program(vault::id(), bytes).unwrap();

        let owner = Keypair::new();
        let guardian = Keypair::new();
        let agent = Keypair::new();
        let payee = Keypair::new();
        let stranger = Keypair::new();
        for kp in [&owner, &guardian, &agent, &payee, &stranger] {
            svm.airdrop(&kp.pubkey(), 10_000_000_000).unwrap();
        }

        let mint = Pubkey::new_unique();
        let vault = vault_pda(&owner.pubkey());
        let mut env = Env {
            vault_ata: get_associated_token_address(&vault, &mint),
            allowance: allowance_pda(&vault, AGENT_ID),
            owner_ata: get_associated_token_address(&owner.pubkey(), &mint),
            agent_ata: get_associated_token_address(&agent.pubkey(), &mint),
            payee_ata: get_associated_token_address(&payee.pubkey(), &mint),
            stranger_ata: get_associated_token_address(&stranger.pubkey(), &mint),
            svm,
            owner,
            guardian,
            agent,
            payee,
            stranger,
            mint,
            vault,
        };
        env.set_time(T0);
        env.put_token_program_account(mint, mint_bytes(6));
        env.put_token_account(env.owner_ata, env.owner.pubkey(), DEPOSIT);
        env.put_token_account(env.agent_ata, env.agent.pubkey(), 0);
        env.put_token_account(env.payee_ata, env.payee.pubkey(), 0);
        env.put_token_account(env.stranger_ata, env.stranger.pubkey(), 0);

        env.initialize_vault().expect("initialize_vault");
        env.deposit(DEPOSIT).expect("deposit");
        let policy = env.default_policy();
        env.create_agent(AGENT_ID, env.agent.pubkey(), policy)
            .expect("create_agent");
        env
    }

    pub fn default_policy(&self) -> AgentPolicy {
        AgentPolicy {
            expires_at: T0 + 7 * DAY,
            per_tx_cap: PER_TX_CAP,
            daily_cap: DAILY_CAP,
            allowlist: vec![self.payee.pubkey()],
            mandate_hash: [7u8; 32],
        }
    }

    // ------------------------------------------------------------ plumbing

    fn put_token_program_account(&mut self, address: Pubkey, data: Vec<u8>) {
        let lamports = self.svm.minimum_balance_for_rent_exemption(data.len());
        self.svm
            .set_account(
                address,
                Account {
                    lamports,
                    data,
                    owner: anchor_spl::token::ID,
                    executable: false,
                    rent_epoch: 0,
                },
            )
            .unwrap();
    }

    pub fn put_token_account(&mut self, address: Pubkey, owner: Pubkey, amount: u64) {
        let data = token_account_bytes(&self.mint, &owner, amount);
        self.put_token_program_account(address, data);
    }

    pub fn now(&self) -> i64 {
        self.svm.get_sysvar::<Clock>().unix_timestamp
    }

    pub fn set_time(&mut self, unix_timestamp: i64) {
        let mut clock = self.svm.get_sysvar::<Clock>();
        clock.unix_timestamp = unix_timestamp;
        self.svm.set_sysvar::<Clock>(&clock);
    }

    pub fn advance(&mut self, seconds: i64) {
        let now = self.now();
        self.set_time(now + seconds);
    }

    pub fn send(&mut self, ix: Instruction, signer: &Keypair) -> TxResult {
        // A fresh blockhash per transaction so identical instructions are not
        // rejected as duplicates.
        self.svm.expire_blockhash();
        let blockhash = self.svm.latest_blockhash();
        let msg = Message::new_with_blockhash(&[ix], Some(&signer.pubkey()), &blockhash);
        let tx = VersionedTransaction::try_new(VersionedMessage::Legacy(msg), &[signer]).unwrap();
        self.svm.send_transaction(tx)
    }

    pub fn balance(&self, token_account: &Pubkey) -> u64 {
        let account = self.svm.get_account(token_account).expect("token account");
        u64::from_le_bytes(account.data[64..72].try_into().unwrap())
    }

    pub fn vault_state(&self) -> Vault {
        let account = self.svm.get_account(&self.vault).unwrap();
        Vault::try_deserialize(&mut account.data.as_slice()).unwrap()
    }

    pub fn allowance_state(&self) -> AgentAllowance {
        self.allowance_state_of(&self.allowance)
    }

    pub fn allowance_state_of(&self, allowance: &Pubkey) -> AgentAllowance {
        let account = self.svm.get_account(allowance).unwrap();
        AgentAllowance::try_deserialize(&mut account.data.as_slice()).unwrap()
    }

    // -------------------------------------------------------- instructions

    pub fn initialize_vault(&mut self) -> TxResult {
        let ix = Instruction::new_with_bytes(
            vault::id(),
            &vault::instruction::InitializeVault {
                guardian: self.guardian.pubkey(),
            }
            .data(),
            vault::accounts::InitializeVault {
                owner: self.owner.pubkey(),
                vault: self.vault,
                usdc_mint: self.mint,
                usdc_vault_ata: self.vault_ata,
                token_program: anchor_spl::token::ID,
                associated_token_program: anchor_spl::associated_token::ID,
                system_program: system_program::ID,
            }
            .to_account_metas(None),
        );
        let owner = self.owner.insecure_clone();
        self.send(ix, &owner)
    }

    pub fn deposit(&mut self, amount: u64) -> TxResult {
        let ix = Instruction::new_with_bytes(
            vault::id(),
            &vault::instruction::Deposit { amount }.data(),
            vault::accounts::Deposit {
                depositor: self.owner.pubkey(),
                vault: self.vault,
                depositor_token_account: self.owner_ata,
                usdc_vault_ata: self.vault_ata,
                usdc_mint: self.mint,
                token_program: anchor_spl::token::ID,
            }
            .to_account_metas(None),
        );
        let owner = self.owner.insecure_clone();
        self.send(ix, &owner)
    }

    pub fn create_agent(
        &mut self,
        agent_id: u64,
        session_key: Pubkey,
        policy: AgentPolicy,
    ) -> TxResult {
        let owner = self.owner.insecure_clone();
        self.create_agent_as(&owner, agent_id, session_key, policy)
    }

    pub fn create_agent_as(
        &mut self,
        signer: &Keypair,
        agent_id: u64,
        session_key: Pubkey,
        policy: AgentPolicy,
    ) -> TxResult {
        let ix = Instruction::new_with_bytes(
            vault::id(),
            &vault::instruction::CreateAgent {
                agent_id,
                session_key,
                policy,
            }
            .data(),
            vault::accounts::CreateAgent {
                owner: signer.pubkey(),
                vault: self.vault,
                allowance: allowance_pda(&self.vault, agent_id),
                system_program: system_program::ID,
            }
            .to_account_metas(None),
        );
        self.send(ix, signer)
    }

    /// `top_up` signed by `signer`, against `allowance`, landing in `destination`.
    pub fn top_up_with(
        &mut self,
        signer: &Keypair,
        allowance: Pubkey,
        destination: Pubkey,
        amount: u64,
    ) -> TxResult {
        let ix = Instruction::new_with_bytes(
            vault::id(),
            &vault::instruction::TopUp { amount }.data(),
            vault::accounts::TopUp {
                agent: signer.pubkey(),
                vault: self.vault,
                allowance,
                usdc_vault_ata: self.vault_ata,
                agent_token_account: destination,
                usdc_mint: self.mint,
                token_program: anchor_spl::token::ID,
            }
            .to_account_metas(None),
        );
        self.send(ix, signer)
    }

    pub fn top_up(&mut self, amount: u64) -> TxResult {
        let agent = self.agent.insecure_clone();
        self.top_up_with(&agent, self.allowance, self.agent_ata, amount)
    }

    pub fn pay_with(&mut self, signer: &Keypair, payee_token_account: Pubkey, amount: u64) -> TxResult {
        let ix = Instruction::new_with_bytes(
            vault::id(),
            &vault::instruction::Pay { amount }.data(),
            vault::accounts::Pay {
                agent: signer.pubkey(),
                vault: self.vault,
                allowance: self.allowance,
                usdc_vault_ata: self.vault_ata,
                payee_token_account,
                usdc_mint: self.mint,
                token_program: anchor_spl::token::ID,
            }
            .to_account_metas(None),
        );
        self.send(ix, signer)
    }

    pub fn pay(&mut self, payee_token_account: Pubkey, amount: u64) -> TxResult {
        let agent = self.agent.insecure_clone();
        self.pay_with(&agent, payee_token_account, amount)
    }

    pub fn freeze_agent(&mut self, authority: &Keypair) -> TxResult {
        let ix = Instruction::new_with_bytes(
            vault::id(),
            &vault::instruction::FreezeAgent {}.data(),
            vault::accounts::FreezeAgent {
                authority: authority.pubkey(),
                vault: self.vault,
                allowance: self.allowance,
            }
            .to_account_metas(None),
        );
        self.send(ix, authority)
    }

    pub fn freeze_vault(&mut self, authority: &Keypair) -> TxResult {
        let ix = Instruction::new_with_bytes(
            vault::id(),
            &vault::instruction::FreezeVault {}.data(),
            vault::accounts::FreezeVault {
                authority: authority.pubkey(),
                vault: self.vault,
            }
            .to_account_metas(None),
        );
        self.send(ix, authority)
    }

    fn owner_agent_accounts(&self, signer: &Keypair) -> vault::accounts::OwnerAgentAction {
        vault::accounts::OwnerAgentAction {
            owner: signer.pubkey(),
            vault: self.vault,
            allowance: self.allowance,
        }
    }

    pub fn unfreeze_agent(&mut self, signer: &Keypair) -> TxResult {
        let ix = Instruction::new_with_bytes(
            vault::id(),
            &vault::instruction::UnfreezeAgent {}.data(),
            self.owner_agent_accounts(signer).to_account_metas(None),
        );
        self.send(ix, signer)
    }

    pub fn unfreeze_vault(&mut self, signer: &Keypair) -> TxResult {
        let ix = Instruction::new_with_bytes(
            vault::id(),
            &vault::instruction::UnfreezeVault {}.data(),
            vault::accounts::OwnerVaultAction {
                owner: signer.pubkey(),
                vault: self.vault,
            }
            .to_account_metas(None),
        );
        self.send(ix, signer)
    }

    pub fn update_policy(&mut self, signer: &Keypair, policy: AgentPolicy) -> TxResult {
        let ix = Instruction::new_with_bytes(
            vault::id(),
            &vault::instruction::UpdatePolicy { policy }.data(),
            self.owner_agent_accounts(signer).to_account_metas(None),
        );
        self.send(ix, signer)
    }

    pub fn rotate_session_key(
        &mut self,
        signer: &Keypair,
        new_session_key: Pubkey,
        new_expires_at: i64,
    ) -> TxResult {
        let ix = Instruction::new_with_bytes(
            vault::id(),
            &vault::instruction::RotateSessionKey {
                new_session_key,
                new_expires_at,
            }
            .data(),
            self.owner_agent_accounts(signer).to_account_metas(None),
        );
        self.send(ix, signer)
    }

    pub fn withdraw(&mut self, signer: &Keypair, destination: Pubkey, amount: u64) -> TxResult {
        let ix = Instruction::new_with_bytes(
            vault::id(),
            &vault::instruction::Withdraw { amount }.data(),
            vault::accounts::Withdraw {
                owner: signer.pubkey(),
                vault: self.vault,
                usdc_vault_ata: self.vault_ata,
                owner_token_account: destination,
                usdc_mint: self.mint,
                token_program: anchor_spl::token::ID,
            }
            .to_account_metas(None),
        );
        self.send(ix, signer)
    }
}

/// The transaction must fail with exactly this program error.
#[track_caller]
pub fn assert_vault_error(result: TxResult, expected: VaultError) {
    let code: u32 = expected.into();
    match result {
        Ok(_) => panic!("expected error {code}, but the transaction succeeded"),
        Err(failure) => {
            let rendered = format!("{:?}", failure.err);
            assert!(
                rendered.contains(&format!("Custom({code})")),
                "expected Custom({code}), got {rendered}\nlogs: {:#?}",
                failure.meta.logs
            );
        }
    }
}

/// The transaction must fail (any reason, e.g. an Anchor account constraint).
#[track_caller]
pub fn assert_rejected(result: TxResult) {
    assert!(result.is_err(), "expected the transaction to be rejected");
}
