'use client';

import React, { useState } from 'react';
import { PageLayout } from '@/components/ui/PageLayout';
import { GlassCard } from '@/components/ui/GlassCard';
import { DataTable, Column } from '@/components/ui/DataTable';
import { useAccountsList, useCreateAccount } from '@/api/hooks/useAccounts';
import { useOwnersList, useCreateOwner } from '@/api/hooks/useOwners';
import { AccountOut, OwnerOut } from '@/api/generated/model';
import { Plus, UserPlus, CreditCard, AlertCircle, CheckCircle } from 'lucide-react';

export default function AccountsPage() {
  const { data: accounts = [], isLoading: accountsLoading } = useAccountsList();
  const { data: owners = [], isLoading: ownersLoading } = useOwnersList();
  const createOwnerMutation = useCreateOwner();
  const createAccountMutation = useCreateAccount();

  // Owner Form State
  const [newOwnerName, setNewOwnerName] = useState('');
  const [ownerError, setOwnerError] = useState<string | null>(null);

  // Account Wizard State
  const [accountName, setAccountName] = useState('');
  const [last4, setLast4] = useState('');
  const [selectedOwnerId, setSelectedOwnerId] = useState<number | undefined>();
  const [accountKind, setAccountKind] = useState<'credit_card' | 'depository'>('credit_card');
  const [csvHeaders, setCsvHeaders] = useState<string[]>([]);
  const [headerInputText, setHeaderInputText] = useState('');
  
  // Mapping columns
  const [dateCol, setDateCol] = useState('');
  const [descCol, setDescCol] = useState('');
  const [amountCol, setAmountCol] = useState('');
  const [typeCol, setTypeCol] = useState('');
  const [catCol, setCatCol] = useState('');
  const [merchantCol, setMerchantCol] = useState('');
  const [ownerCol, setOwnerCol] = useState('');
  const [signConvention, setSignConvention] = useState<string>('');
  const [accountError, setAccountError] = useState<string | null>(null);
  const [accountSuccess, setAccountSuccess] = useState<string | null>(null);

  const handleCreateOwner = async (e: React.FormEvent) => {
    e.preventDefault();
    setOwnerError(null);
    if (!newOwnerName.trim()) return;

    try {
      await createOwnerMutation.mutateAsync({ name: newOwnerName.trim() });
      setNewOwnerName('');
    } catch (err: any) {
      if (err.isDuplicate || err.status === 409) {
        setOwnerError(`Owner "${newOwnerName}" already exists.`);
      } else {
        setOwnerError(err.message || 'Failed to create owner.');
      }
    }
  };

  const handleParseCsvHeader = (text: string) => {
    setHeaderInputText(text);
    if (!text.trim()) {
      setCsvHeaders([]);
      return;
    }
    const firstLine = text.trim().split('\n')[0];
    const headers = firstLine.split(',').map((h) => h.replace(/^["']|["']$/g, '').trim());
    setCsvHeaders(headers);

    // Auto-detect common headers
    headers.forEach((h) => {
      const lower = h.toLowerCase();
      if (lower.includes('date') && !dateCol) setDateCol(h);
      if ((lower.includes('desc') || lower.includes('memo') || lower.includes('payee')) && !descCol) setDescCol(h);
      if (lower.includes('amount') && !amountCol) setAmountCol(h);
      if (lower.includes('type') && !typeCol) setTypeCol(h);
      if (lower.includes('category') && !catCol) setCatCol(h);
      if (lower.includes('merchant') && !merchantCol) setMerchantCol(h);
    });
  };

  const handleCreateAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    setAccountError(null);
    setAccountSuccess(null);

    if (!accountName.trim() || !last4.trim()) {
      setAccountError('Account name and last 4 digits are required.');
      return;
    }
    if (!dateCol || !descCol || !amountCol) {
      setAccountError('Date, Description, and Amount columns are required in column mapping.');
      return;
    }
    // Validation rule: at least one of type_col or sign_convention must be present (INV-28)
    if (!typeCol && !signConvention) {
      setAccountError('At least one of Type Column or Sign Convention is required to resolve transaction signs.');
      return;
    }

    try {
      const defaultMapping: any = {
        date_col: dateCol,
        description_col: descCol,
        amount_col: amountCol,
      };
      if (typeCol) defaultMapping.type_col = typeCol;
      if (catCol) defaultMapping.category_col = catCol;
      if (merchantCol) defaultMapping.merchant_col = merchantCol;
      if (ownerCol) defaultMapping.owner_col = ownerCol;
      if (signConvention) defaultMapping.sign_convention = signConvention;

      await createAccountMutation.mutateAsync({
        name: accountName.trim(),
        last4: last4.trim(),
        default_owner_id: selectedOwnerId,
        source_format: 'csv',
        account_kind: accountKind,
        default_mapping: defaultMapping,
      });

      setAccountSuccess(`Account "${accountName}" created successfully!`);
      setAccountName('');
      setLast4('');
      setHeaderInputText('');
      setCsvHeaders([]);
      setDateCol('');
      setDescCol('');
      setAmountCol('');
      setTypeCol('');
      setCatCol('');
      setMerchantCol('');
      setOwnerCol('');
      setSignConvention('');
    } catch (err: any) {
      setAccountError(err.message || 'Failed to create account.');
    }
  };

  const accountColumns: Column<AccountOut>[] = [
    { header: 'ID', accessorKey: 'id', className: 'w-12 font-mono text-xs' },
    { header: 'Account Name', accessorKey: 'name', className: 'font-semibold' },
    {
      header: 'Last 4',
      cell: (a) => <span className="font-mono text-xs text-muted-foreground">•••• {a.last4}</span>,
    },
    {
      header: 'Kind',
      cell: (a) => (
        <span className="rounded bg-secondary px-2 py-0.5 text-xs text-secondary-foreground uppercase">
          {a.account_kind}
        </span>
      ),
    },
    {
      header: 'Default Owner',
      cell: (a) => {
        const owner = owners.find((o) => o.id === a.default_owner_id);
        return <span>{owner ? owner.name : '(none)'}</span>;
      },
    },
  ];

  return (
    <PageLayout
      title="Accounts & Owners"
      description="Register accounts, establish column-mapping profiles, and manage owners"
      breadcrumbs={[{ label: 'Accounts' }]}
    >
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column: Registered Owners and Accounts list */}
        <div className="lg:col-span-2 space-y-6">
          <GlassCard>
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-foreground flex items-center gap-2">
                <CreditCard className="h-4 w-4 text-primary" />
                <span>Registered Accounts ({accounts.length})</span>
              </h3>
            </div>
            <div className="mb-3 rounded bg-secondary/30 p-2.5 text-xs text-muted-foreground">
              <strong>Note:</strong> Account profiles store column mapping templates for CSV imports.
              AccountOut omits default_mapping by design, so mapping templates cannot be edited once created.
            </div>
            <DataTable
              data={accounts}
              columns={accountColumns}
              keyExtractor={(a) => a.id}
              emptyMessage="No accounts registered yet. Use the wizard on the right to add your first account."
            />
          </GlassCard>

          {/* Owners section */}
          <GlassCard>
            <h3 className="font-semibold text-foreground mb-3 flex items-center gap-2">
              <UserPlus className="h-4 w-4 text-primary" />
              <span>Registered Owners ({owners.length})</span>
            </h3>

            <form onSubmit={handleCreateOwner} className="flex gap-2 mb-4">
              <div className="flex-1">
                <input
                  type="text"
                  placeholder="New owner name (e.g. Pat, Chris)"
                  value={newOwnerName}
                  onChange={(e) => setNewOwnerName(e.target.value)}
                  className="w-full rounded-lg border border-border/80 bg-secondary/40 px-3 py-2 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
                {ownerError && (
                  <p className="mt-1 text-xs text-rose-400 flex items-center gap-1">
                    <AlertCircle className="h-3 w-3" />
                    <span>{ownerError}</span>
                  </p>
                )}
              </div>
              <button
                type="submit"
                disabled={!newOwnerName.trim() || createOwnerMutation.isPending}
                className="rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                Add Owner
              </button>
            </form>

            <div className="flex flex-wrap gap-2">
              {owners.map((o) => (
                <div
                  key={o.id}
                  className="rounded-md border border-border/60 bg-secondary/50 px-3 py-1 text-xs font-medium text-foreground flex items-center gap-2"
                >
                  <span>{o.name}</span>
                  <span className="text-[10px] text-muted-foreground font-mono">#{o.id}</span>
                </div>
              ))}
            </div>
          </GlassCard>
        </div>

        {/* Right Column: New Account & Column Mapping Wizard */}
        <div>
          <GlassCard className="space-y-4">
            <h3 className="font-semibold text-foreground flex items-center gap-2">
              <Plus className="h-4 w-4 text-primary" />
              <span>Create Account Wizard</span>
            </h3>
            <p className="text-xs text-muted-foreground">
              Define the bank CSV structure and accounting classification rules for imports.
            </p>

            <form onSubmit={handleCreateAccount} className="space-y-3">
              {accountError && (
                <div className="rounded bg-rose-500/10 border border-rose-500/30 p-2.5 text-xs text-rose-300 flex items-center gap-1.5">
                  <AlertCircle className="h-4 w-4 flex-shrink-0" />
                  <span>{accountError}</span>
                </div>
              )}
              {accountSuccess && (
                <div className="rounded bg-emerald-500/10 border border-emerald-500/30 p-2.5 text-xs text-emerald-300 flex items-center gap-1.5">
                  <CheckCircle className="h-4 w-4 flex-shrink-0" />
                  <span>{accountSuccess}</span>
                </div>
              )}

              <div>
                <label className="text-xs font-medium text-foreground">Account Name</label>
                <input
                  type="text"
                  placeholder="e.g. Chase Sapphire, BoA Checking"
                  value={accountName}
                  onChange={(e) => setAccountName(e.target.value)}
                  className="mt-1 w-full rounded-md border border-border bg-secondary/40 px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-xs font-medium text-foreground">Last 4 Digits</label>
                  <input
                    type="text"
                    maxLength={4}
                    placeholder="1234"
                    value={last4}
                    onChange={(e) => setLast4(e.target.value)}
                    className="mt-1 w-full font-mono rounded-md border border-border bg-secondary/40 px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-foreground">Default Owner</label>
                  <select
                    value={selectedOwnerId ?? ''}
                    onChange={(e) => setSelectedOwnerId(e.target.value ? Number(e.target.value) : undefined)}
                    className="mt-1 w-full rounded-md border border-border bg-secondary/40 px-2 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                  >
                    <option value="">(None)</option>
                    {owners.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Account Kind Selection (Required user choice) */}
              <div className="rounded-md border border-border/70 p-2.5 bg-secondary/20">
                <label className="text-xs font-semibold text-foreground block mb-1">
                  Account Kind (Required Choice)
                </label>
                <p className="text-[11px] text-muted-foreground mb-2">
                  Credit Card vs Depository (Checking/Savings). This is deliberately NOT inferred from
                  type columns (Chase checking CSVs include a 'Type' column).
                </p>
                <div className="flex gap-4">
                  <label className="flex items-center gap-1.5 text-xs text-foreground cursor-pointer">
                    <input
                      type="radio"
                      name="accountKind"
                      checked={accountKind === 'credit_card'}
                      onChange={() => setAccountKind('credit_card')}
                    />
                    <span>Credit Card</span>
                  </label>
                  <label className="flex items-center gap-1.5 text-xs text-foreground cursor-pointer">
                    <input
                      type="radio"
                      name="accountKind"
                      checked={accountKind === 'depository'}
                      onChange={() => setAccountKind('depository')}
                    />
                    <span>Depository (Checking/Savings)</span>
                  </label>
                </div>
              </div>

              {/* Paste CSV Sample / Header Wizard */}
              <div className="border-t border-border/40 pt-3">
                <label className="text-xs font-semibold text-foreground block">
                  Column Mapping Wizard
                </label>
                <p className="text-[11px] text-muted-foreground mb-2">
                  Paste the first row (headers) of your bank CSV to select columns:
                </p>
                <textarea
                  rows={2}
                  value={headerInputText}
                  onChange={(e) => handleParseCsvHeader(e.target.value)}
                  placeholder="Date,Description,Amount,Category,Type"
                  className="w-full rounded-md border border-border bg-secondary/40 p-2 font-mono text-[11px] text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />

                <div className="mt-3 space-y-2">
                  {[
                    { label: 'Date Column *', val: dateCol, set: setDateCol, req: true },
                    { label: 'Description Column *', val: descCol, set: setDescCol, req: true },
                    { label: 'Amount Column *', val: amountCol, set: setAmountCol, req: true },
                    { label: 'Type Column', val: typeCol, set: setTypeCol },
                    { label: 'Category Column', val: catCol, set: setCatCol },
                    { label: 'Merchant Column', val: merchantCol, set: setMerchantCol },
                    { label: 'Owner Column', val: ownerCol, set: setOwnerCol },
                  ].map((field, idx) => (
                    <div key={idx} className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">{field.label}</span>
                      {csvHeaders.length > 0 ? (
                        <select
                          value={field.val}
                          onChange={(e) => field.set(e.target.value)}
                          className="w-40 rounded border border-border bg-secondary px-2 py-1 text-xs text-foreground"
                        >
                          <option value="">(Select)</option>
                          {csvHeaders.map((h) => (
                            <option key={h} value={h}>
                              {h}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type="text"
                          value={field.val}
                          onChange={(e) => field.set(e.target.value)}
                          placeholder="Header name"
                          className="w-40 rounded border border-border bg-secondary/50 px-2 py-1 text-xs text-foreground"
                        />
                      )}
                    </div>
                  ))}

                  <div className="flex items-center justify-between text-xs pt-1">
                    <span className="text-muted-foreground">Sign Convention</span>
                    <select
                      value={signConvention}
                      onChange={(e) => setSignConvention(e.target.value)}
                      className="w-40 rounded border border-border bg-secondary px-2 py-1 text-xs text-foreground"
                    >
                      <option value="">(None / From Type)</option>
                      <option value="negative_is_spend">Negative is Spend</option>
                      <option value="positive_is_spend">Positive is Spend</option>
                    </select>
                  </div>
                </div>
              </div>

              <button
                type="submit"
                disabled={createAccountMutation.isPending}
                className="w-full rounded-lg bg-primary py-2 text-xs font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                Create Account Profile
              </button>
            </form>
          </GlassCard>
        </div>
      </div>
    </PageLayout>
  );
}
