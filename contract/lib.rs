#![cfg_attr(not(feature = "std"), no_std, no_main)]

#[ink::contract]
mod dotgrants {
    use ink::storage::Mapping;
    use ink::prelude::vec::Vec;

    #[derive(Debug, Clone, PartialEq, Eq)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    #[cfg_attr(feature = "std", derive(ink::storage::traits::StorageLayout))]
    pub enum GrantStatus { Open, Approved, Reclaimed }

    #[derive(Debug, Clone)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    #[cfg_attr(feature = "std", derive(ink::storage::traits::StorageLayout))]
    pub struct Grant {
        pub funder: AccountId,
        pub amount: Balance,
        pub metadata_hash: [u8; 32],
        pub deadline: Timestamp,
        pub status: GrantStatus,
        pub approved_builder: Option<AccountId>,
    }

    #[derive(Debug, Clone)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    #[cfg_attr(feature = "std", derive(ink::storage::traits::StorageLayout))]
    pub struct Application {
        pub applicant: AccountId,
        pub proposal_hash: [u8; 32],
    }

    #[derive(Debug, PartialEq, Eq)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    pub enum Error {
        GrantNotFound, NotFunder, GrantNotOpen,
        DeadlineNotPassed, DeadlinePassed, NotAnApplicant,
        TransferFailed, ZeroAmount,
    }

    pub type Result<T> = core::result::Result<T, Error>;

    // Using flat mappings instead of Mapping<u64, Vec<Application>>
    // because Vec<T> Packed constraint is complex in ink! 5
    #[ink(storage)]
    pub struct Dotgrants {
        grants: Mapping<u64, Grant>,
        // (grant_id, index) -> Application
        applications: Mapping<(u64, u64), Application>,
        // grant_id -> application count
        app_counts: Mapping<u64, u64>,
        next_grant_id: u64,
    }

    #[ink(event)]
    pub struct GrantCreated {
        #[ink(topic)] grant_id: u64,
        funder: AccountId,
        amount: Balance,
    }

    #[ink(event)]
    pub struct ApplicationSubmitted {
        #[ink(topic)] grant_id: u64,
        applicant: AccountId,
    }

    #[ink(event)]
    pub struct GrantApproved {
        #[ink(topic)] grant_id: u64,
        builder: AccountId,
        amount: Balance,
    }

    #[ink(event)]
    pub struct GrantReclaimed {
        #[ink(topic)] grant_id: u64,
        funder: AccountId,
        amount: Balance,
    }

    impl Dotgrants {
        #[ink(constructor)]
        pub fn new() -> Self {
            Self {
                grants: Mapping::default(),
                applications: Mapping::default(),
                app_counts: Mapping::default(),
                next_grant_id: 0,
            }
        }

        #[ink(message, payable)]
        pub fn create_grant(&mut self, metadata_hash: [u8; 32], deadline: Timestamp) -> Result<u64> {
            let amount = self.env().transferred_value();
            if amount == 0 { return Err(Error::ZeroAmount); }
            let grant_id = self.next_grant_id;
            let funder = self.env().caller();
            self.grants.insert(grant_id, &Grant {
                funder, amount, metadata_hash, deadline,
                status: GrantStatus::Open, approved_builder: None,
            });
            self.app_counts.insert(grant_id, &0u64);
            self.next_grant_id = self.next_grant_id.saturating_add(1);
            self.env().emit_event(GrantCreated { grant_id, funder, amount });
            Ok(grant_id)
        }

        #[ink(message)]
        pub fn apply_for_grant(&mut self, grant_id: u64, proposal_hash: [u8; 32]) -> Result<()> {
            let grant = self.grants.get(grant_id).ok_or(Error::GrantNotFound)?;
            if grant.status != GrantStatus::Open { return Err(Error::GrantNotOpen); }
            if self.env().block_timestamp() > grant.deadline { return Err(Error::DeadlinePassed); }
            let applicant = self.env().caller();
            let idx = self.app_counts.get(grant_id).unwrap_or(0);
            self.applications.insert((grant_id, idx), &Application { applicant, proposal_hash });
            self.app_counts.insert(grant_id, &idx.saturating_add(1));
            self.env().emit_event(ApplicationSubmitted { grant_id, applicant });
            Ok(())
        }

        #[ink(message)]
        pub fn approve_applicant(&mut self, grant_id: u64, builder: AccountId) -> Result<()> {
            let mut grant = self.grants.get(grant_id).ok_or(Error::GrantNotFound)?;
            if grant.funder != self.env().caller() { return Err(Error::NotFunder); }
            if grant.status != GrantStatus::Open { return Err(Error::GrantNotOpen); }
            // verify builder actually applied
            let count = self.app_counts.get(grant_id).unwrap_or(0);
            let applied = (0..count).any(|i| {
                self.applications.get((grant_id, i))
                    .map(|a| a.applicant == builder)
                    .unwrap_or(false)
            });
            if !applied { return Err(Error::NotAnApplicant); }
            grant.status = GrantStatus::Approved;
            grant.approved_builder = Some(builder);
            self.grants.insert(grant_id, &grant);
            self.env().transfer(builder, grant.amount).map_err(|_| Error::TransferFailed)?;
            self.env().emit_event(GrantApproved { grant_id, builder, amount: grant.amount });
            Ok(())
        }

        #[ink(message)]
        pub fn reclaim(&mut self, grant_id: u64) -> Result<()> {
            let mut grant = self.grants.get(grant_id).ok_or(Error::GrantNotFound)?;
            if grant.funder != self.env().caller() { return Err(Error::NotFunder); }
            if grant.status != GrantStatus::Open { return Err(Error::GrantNotOpen); }
            if self.env().block_timestamp() <= grant.deadline { return Err(Error::DeadlineNotPassed); }
            grant.status = GrantStatus::Reclaimed;
            self.grants.insert(grant_id, &grant);
            let funder = grant.funder;
            self.env().transfer(funder, grant.amount).map_err(|_| Error::TransferFailed)?;
            self.env().emit_event(GrantReclaimed { grant_id, funder, amount: grant.amount });
            Ok(())
        }

        #[ink(message)]
        pub fn get_grant(&self, grant_id: u64) -> Option<Grant> { self.grants.get(grant_id) }

        #[ink(message)]
        pub fn get_application(&self, grant_id: u64, idx: u64) -> Option<Application> {
            self.applications.get((grant_id, idx))
        }

        #[ink(message)]
        pub fn get_application_count(&self, grant_id: u64) -> u64 {
            self.app_counts.get(grant_id).unwrap_or(0)
        }

        #[ink(message)]
        pub fn get_grant_count(&self) -> u64 { self.next_grant_id }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        fn alice() -> AccountId {
            ink::env::test::default_accounts::<ink::env::DefaultEnvironment>().alice
        }
        fn bob() -> AccountId {
            ink::env::test::default_accounts::<ink::env::DefaultEnvironment>().bob
        }
        fn charlie() -> AccountId {
            ink::env::test::default_accounts::<ink::env::DefaultEnvironment>().charlie
        }

        #[ink::test]
        fn create_grant_works() {
            let mut c = Dotgrants::new();
            ink::env::test::set_value_transferred::<ink::env::DefaultEnvironment>(1000);
            assert_eq!(c.create_grant([0u8; 32], 9_999_999_999), Ok(0));
            assert_eq!(c.get_grant_count(), 1);
            let g = c.get_grant(0).unwrap();
            assert_eq!(g.amount, 1000);
            assert_eq!(g.status, GrantStatus::Open);
        }

        #[ink::test]
        fn apply_works() {
            let mut c = Dotgrants::new();
            ink::env::test::set_value_transferred::<ink::env::DefaultEnvironment>(1000);
            c.create_grant([0u8; 32], 9_999_999_999).unwrap();
            ink::env::test::set_caller::<ink::env::DefaultEnvironment>(bob());
            assert_eq!(c.apply_for_grant(0, [1u8; 32]), Ok(()));
            assert_eq!(c.get_application_count(0), 1);
            let app = c.get_application(0, 0).unwrap();
            assert_eq!(app.applicant, bob());
        }

        #[ink::test]
        fn zero_amount_fails() {
            let mut c = Dotgrants::new();
            assert_eq!(c.create_grant([0u8; 32], 9_999_999_999), Err(Error::ZeroAmount));
        }

        #[ink::test]
        fn only_funder_can_approve() {
            let mut c = Dotgrants::new();
            ink::env::test::set_value_transferred::<ink::env::DefaultEnvironment>(1000);
            c.create_grant([0u8; 32], 9_999_999_999).unwrap();
            ink::env::test::set_caller::<ink::env::DefaultEnvironment>(bob());
            c.apply_for_grant(0, [1u8; 32]).unwrap();
            ink::env::test::set_caller::<ink::env::DefaultEnvironment>(charlie());
            assert_eq!(c.approve_applicant(0, bob()), Err(Error::NotFunder));
        }

        #[ink::test]
        fn non_applicant_cannot_be_approved() {
            let mut c = Dotgrants::new();
            ink::env::test::set_value_transferred::<ink::env::DefaultEnvironment>(1000);
            c.create_grant([0u8; 32], 9_999_999_999).unwrap();
            // bob never applied
            assert_eq!(c.approve_applicant(0, bob()), Err(Error::NotAnApplicant));
        }
    }
}
