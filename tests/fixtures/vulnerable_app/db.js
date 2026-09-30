const { Pool } = require('pg');

const pool = new Pool({ connectionString: 'postgres://admin:admin@localhost:5432/app' });

module.exports = {
  query: async (sql) => (await pool.query(sql)).rows,
};
