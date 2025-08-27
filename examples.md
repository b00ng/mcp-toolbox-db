total value of orders for the customer id 29

all orders of customer id 29


sales by month
sales volume over time
sale volume from Jun 2024 to Oct 2024


customers.address

2. "List all products that were included in orders with a 'shipped' status during the last quarter."	

SELECT DISTINCT p.* FROM products p JOIN order_items oi ON p.id = oi.product_id JOIN orders o ON oi.order_id = o.id WHERE o.status = 'shipped' AND o.created_at >= date('now', '-3 months');


3. "Which customers have ordered a 'Gaming Mouse' but have never ordered a 'Mechanical Keyboard'?"	

SELECT c.* FROM customers c WHERE c.id IN (SELECT o.customer_id FROM orders o JOIN order_items oi ON o.id = oi.order_id JOIN products p ON oi.product_id = p.id WHERE p.name = 'Gaming Mouse') AND c.id NOT IN (SELECT o.customer_id FROM orders o JOIN order_items oi ON o.id = oi.order_id JOIN products p ON oi.product_id = p.id WHERE p.name = 'Mechanical Keyboard');

0 row

4. "Find all orders placed in 'January 2024' that contain products with a stock level below 20."	

SELECT o.* FROM orders o JOIN order_items oi ON o.id = oi.order_id JOIN products p ON oi.product_id = p.id WHERE strftime('%Y-%m', o.created_at) = '2024-01' AND p.stock < 20;

0 row

5. "List all products with a price higher than the average price of all products in the store."	

SELECT * FROM products WHERE price_cents > (SELECT AVG(price_cents) FROM products);


6. "Show me all customers whose total spending is greater than the average total spending of all customers."	
SELECT c.id, c.name, SUM(oi.price_cents * oi.quantity) AS total_spent FROM customers c JOIN orders o ON c.id = o.customer_id JOIN order_items oi ON o.id = oi.order_id GROUP BY c.id, c.name HAVING total_spent > (SELECT AVG(total_spent) FROM (SELECT SUM(oi.price_cents * oi.quantity) as total_spent FROM order_items oi GROUP BY oi.order_id));

OK

7. "Find orders where the total value is more than double the average order value for the month of 'March 2024'."	SELECT o.id, SUM(oi.price_cents * oi.quantity) as order_value FROM orders o JOIN order_items oi ON o.id = oi.order_id WHERE strftime('%Y-%m', o.created_at) = '2024-03' GROUP BY o.id HAVING order_value > 2 * (SELECT AVG(order_total) FROM (SELECT SUM(oi.price_cents * oi.quantity) as order_total FROM orders o JOIN order_items oi ON o.id = oi.order_id WHERE strftime('%Y-%m', o.created_at) = '2024-03' GROUP BY o.id));

OK

8. "Which products have a sales volume (units sold) higher than the average sales volume for their product category?"	SELECT p.name, SUM(oi.quantity) as total_quantity_sold FROM products p JOIN order_items oi ON p.id = oi.product_id GROUP BY p.name HAVING total_quantity_sold > (SELECT AVG(quantity_sold) FROM (SELECT SUM(oi.quantity) as quantity_sold FROM order_items oi GROUP BY oi.product_id));

OK

9. "Of the customers who signed up last year, how many have placed more than 5 orders?"	SELECT COUNT(*) FROM (SELECT c.id FROM customers c WHERE strftime('%Y', c.created_at) = strftime('%Y', date('now', '-1 year')) AND (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) > 5);

OK


16. "Identify 'at-risk' customers who have a high lifetime value but haven't purchased anything in the last 90 days."	WITH CustomerSpend AS (SELECT o.customer_id, SUM(oi.price_cents * oi.quantity) as total_spend, MAX(o.created_at) as last_order_date FROM orders o JOIN order_items oi ON o.id = oi.order_id GROUP BY o.customer_id) SELECT cs.* FROM CustomerSpend cs WHERE cs.total_spend > 50000 AND cs.last_order_date < date('now', '-90 days');

17. "Show the top 3 most frequently purchased product combinations in a single order."	WITH OrderPairs AS (SELECT oi1.product_id as p1, oi2.product_id as p2, COUNT(*) as frequency FROM order_items oi1 JOIN order_items oi2 ON oi1.order_id = oi2.order_id WHERE oi1.product_id < oi2.product_id GROUP BY p1, p2) SELECT p1.name, p2.name, op.frequency FROM OrderPairs op JOIN products p1 ON op.p1 = p1.id JOIN products p2 ON op.p2 = p2.id ORDER BY op.frequency DESC LIMIT 3;

OK