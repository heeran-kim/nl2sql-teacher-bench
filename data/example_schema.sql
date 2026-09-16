CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100),
    email VARCHAR(100),
    signup_date DATE
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    total_amount DECIMAL(10, 2),
    status VARCHAR(20),
    created_at DATE,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);
