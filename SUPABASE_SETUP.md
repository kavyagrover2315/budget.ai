# Supabase Integration Setup Guide

## Step 1: Create a Supabase Project

1. Go to [supabase.com](https://supabase.com)
2. Sign up or log in
3. Click **New Project**
4. Fill in project details:
   - **Name**: budgetai
   - **Database Password**: Create a strong password
   - **Region**: Choose closest to your location
5. Wait for the project to initialize (2-3 minutes)

## Step 2: Get Your API Credentials

1. In your Supabase project, go to **Settings → API**
2. Copy these two values:
   - **Project URL** (looks like: `https://your-project.supabase.co`)
   - **anon public key** (starts with `eyJ...`)

## Step 3: Create Users Table

1. In Supabase dashboard, go to **SQL Editor**
2. Click **New Query**
3. Paste this SQL:

```sql
-- Create users table
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email VARCHAR(255) UNIQUE NOT NULL,
  password VARCHAR(255) NOT NULL,
  first_name VARCHAR(255),
  last_name VARCHAR(255),
  mobile VARCHAR(20),
  auth_type VARCHAR(50) DEFAULT 'email',
  picture TEXT,
  gender VARCHAR(50),
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

-- Create index on email for faster lookups
CREATE INDEX idx_users_email ON users(email);

-- Create transactions table
CREATE TABLE transactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  description VARCHAR(255),
  amount DECIMAL(10, 2) NOT NULL,
  type VARCHAR(50), -- 'CREDIT' or 'DEBIT'
  category VARCHAR(100),
  payment_method VARCHAR(100),
  date DATE,
  note TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_transactions_user ON transactions(user_id);
CREATE INDEX idx_transactions_date ON transactions(date);

-- Create statements table
CREATE TABLE statements (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  filename VARCHAR(255),
  transaction_count INT,
  total_income DECIMAL(12, 2),
  total_expense DECIMAL(12, 2),
  net_savings DECIMAL(12, 2),
  savings_rate INT,
  health_score INT,
  analysis_data JSONB,
  uploaded_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_statements_user ON statements(user_id);

-- Create budgets table
CREATE TABLE budgets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category VARCHAR(100),
  limit_amount DECIMAL(10, 2),
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_budgets_user ON budgets(user_id);
```

4. Click **Run** to execute the query

## Step 4: Configure Environment Variables

### Option A: Using .env file (Recommended)

1. Create a `.env` file in the project root:

```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-public-key
```

2. Install python-dotenv:
```bash
pip install python-dotenv
```

3. Add to the top of app.py (after imports):
```python
from dotenv import load_dotenv
load_dotenv()
```

### Option B: Using Environment Variables Directly

Set environment variables in your terminal:

**On Windows (PowerShell):**
```powershell
$env:SUPABASE_URL = "https://znwdslsjgomkhgtqfawv.supabase.co"
$env:SUPABASE_KEY = "sb_publishable_-9ieRh5xUmV5Zwuclphm2A_yRzczC6J"
```

**On Linux/Mac:**
```bash
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_KEY="your-anon-public-key"
```

## Step 5: Install Dependencies

```bash
pip install -r requirements.txt
```

This installs the Supabase Python client library.

## Step 6: Test the Connection

Run the app:
```bash
python app.py
```

If Supabase is connected correctly, you should see in the logs:
```
✓ Supabase connected
```

If it fails, it will automatically fallback to MongoDB with this warning:
```
⚠ Supabase connection failed: ... Falling back to MongoDB.
```

## Step 7: Set Up Supabase Row Level Security (RLS) - Optional but Recommended

1. Go to **Authentication → Policies** in Supabase
2. For `users` table:
   - Users can only view/edit their own record
   - Admins can manage all users

3. For `transactions` table:
   - Users can only view/edit their own transactions

Example policy:
```sql
-- Users can only select their own data
CREATE POLICY "Users can only select their own data" ON transactions
  FOR SELECT USING (auth.uid() = user_id);

-- Users can only insert their own transactions
CREATE POLICY "Users can only insert their own transactions" ON transactions
  FOR INSERT WITH CHECK (auth.uid() = user_id);
```

## Troubleshooting

### Connection Failed
- ✓ Verify SUPABASE_URL is correct (copy from Settings → API)
- ✓ Verify SUPABASE_KEY is correct (anon public key, not service_role key)
- ✓ Check internet connection
- ✓ Verify project is active in Supabase dashboard

### User Not Saving
- ✓ Check if users table was created successfully
- ✓ Verify Supabase connection status in app logs
- ✓ Check browser console for errors

### Password Not Matching
- ✓ Ensure bcrypt is installed (`pip install flask-bcrypt`)
- ✓ Check that password hashing is working

## How It Works

1. **On Signup**: User data is saved to Supabase (with fallback to MongoDB)
2. **On Login**: App checks Supabase first, then MongoDB
3. **Data Storage**: All user info syncs between both databases
4. **Fallback**: If Supabase unavailable, MongoDB is automatically used
5. **Future Uploads**: Statement data is stored in Supabase `statements` table

## Next Steps

- Add row-level security policies for data protection
- Set up authentication with Supabase Auth (optional)
- Configure backup strategy
- Set up monitoring alerts
- Migrate existing MongoDB data to Supabase if needed

## Support

For Supabase help: [docs.supabase.com](https://docs.supabase.com)
For BudgetAI issues: Check app logs for detailed error messages
