import { http, HttpResponse } from 'msw';

export const handlers = [
  // Owners
  http.get('/api/owners', () => {
    return HttpResponse.json([
      { id: 1, name: 'Pat', created_at: '2026-01-01T00:00:00' },
      { id: 2, name: 'Chris', created_at: '2026-01-01T00:00:00' },
    ]);
  }),

  http.post('/api/owners', async ({ request }) => {
    const body = (await request.json()) as any;
    if (body.name === 'Pat') {
      return new HttpResponse(
        JSON.stringify({ detail: `Owner with name 'Pat' already exists` }),
        { status: 409, headers: { 'Content-Type': 'application/json' } }
      );
    }
    return HttpResponse.json({ id: 3, name: body.name, created_at: '2026-01-01T00:00:00' });
  }),

  // Accounts
  http.get('/api/accounts', () => {
    return HttpResponse.json([
      {
        id: 10,
        name: 'Chase Sapphire',
        last4: '1234',
        default_owner_id: 1,
        source_format: 'csv',
        account_kind: 'credit_card',
        created_at: '2026-01-01T00:00:00',
      },
    ]);
  }),

  // Mappings
  http.get('/api/mappings', ({ request }) => {
    const url = new URL(request.url);
    const accountId = url.searchParams.get('account_id');

    if (accountId === '10') {
      return HttpResponse.json([
        {
          id: 101,
          kind: 'merchant',
          raw_value: 'UBER%',
          canonical_value: 'Uber',
          account_id: 10,
        },
      ]);
    }

    // Global mappings (account_id is null)
    return HttpResponse.json([
      {
        id: 201,
        kind: 'category',
        raw_value: 'GROCERIES',
        canonical_value: 'Groceries',
        account_id: null,
      },
    ]);
  }),

  // Transactions
  http.get('/api/transactions', ({ request }) => {
    const url = new URL(request.url);
    const category = url.searchParams.get('category');
    const dateTo = url.searchParams.get('date_to');

    if (category === '(unassigned)') {
      // Server raises 422 UnknownLabelFilterError if raw (unassigned) is sent!
      return new HttpResponse(
        JSON.stringify({ detail: 'UnknownLabelFilterError: No such label (unassigned)' }),
        { status: 422, headers: { 'Content-Type': 'application/json' } }
      );
    }

    return HttpResponse.json({
      transactions: [
        {
          id: 501,
          account_id: 10,
          owner_id: 1,
          transaction_date: '2026-01-15',
          description: 'Trader Joes',
          amount: '84.50',
          transaction_type: 'SPEND',
          effective_type: 'SPEND',
          is_spend: true,
          category_raw: 'GROCERY',
          category_normalized: 'Groceries',
          effective_category: 'Groceries',
        },
      ],
      match_count: 1,
      returned: 1,
      truncated: false,
    });
  }),
];
