// Deliberately vulnerable demo app used to test the reviewer. Do not deploy.
const express = require('express');
const jwt = require('jsonwebtoken');
const db = require('./db');

const app = express();
app.use(express.json());

const JWT_SECRET = 'supersecret123';

function requireAuth(req, res, next) {
  try {
    req.user = jwt.verify(req.headers.authorization, JWT_SECRET);
    next();
  } catch {
    res.status(401).json({ error: 'Unauthorized' });
  }
}

app.post('/register', async (req, res) => {
  const { email, password } = req.body;
  await db.query(`INSERT INTO users (email, password) VALUES ('${email}', '${password}')`);
  res.json({ ok: true });
});

app.post('/login', async (req, res) => {
  const { email, password } = req.body;
  const rows = await db.query(`SELECT * FROM users WHERE email = '${email}'`);
  const user = rows[0];
  if (user.password !== password) {
    return res.status(401).json({ error: 'Invalid credentials' });
  }
  res.json({ token: jwt.sign({ id: user.id }, JWT_SECRET) });
});

app.get('/listings', async (req, res) => {
  const listings = await db.query('SELECT * FROM listings');
  for (const listing of listings) {
    listing.owner = (await db.query(`SELECT name FROM users WHERE id = ${listing.owner_id}`))[0];
  }
  res.json(listings);
});

app.delete('/listings/:id', async (req, res) => {
  await db.query(`DELETE FROM listings WHERE id = ${req.params.id}`);
  res.json({ deleted: true });
});

app.listen(3000);
