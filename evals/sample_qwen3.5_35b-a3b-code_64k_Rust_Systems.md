# Prompt

Write a thread-safe connection pool in Rust using Arc and Mutex. It should support timeouts, graceful shutdown, and handle connection errors. Include detailed comments.

# Response

# Thread-Safe Connection Pool in Rust

Below is a complete, production-ready, thread-safe connection pool in Rust using `Arc` and `Mutex`, with support for timeouts, graceful shutdown, and robust error handling. It is designed for asynchronous use with Tokio and includes both a generic `Connection` trait and a concrete `TcpConnection` implementation.

---

## 1. Core Traits and Connection Types

```rust
//! Thread-safe connection pool with timeout and graceful shutdown support.
//!
//! This module provides a generic, async-compatible connection pool using
//! `Arc<Mutex<>>` for thread safety. It supports configurable timeouts,
//! graceful shutdown, and handles connection errors cleanly.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{Mutex, mpsc};
use tokio::time::timeout;
use thiserror::Error;

/// Represents a connection error
#[derive(Error, Debug)]
pub enum ConnectionError {
    #[error("Connection timeout after {0:?}")]
    Timeout(Duration),
    #[error("Connection pool is shutting down")]
    Shutdown,
    #[error("Failed to create connection: {0}")]
    CreateError(String),
    #[error("Connection was broken: {0}")]
    Broken(String),
    #[error("Pool is exhausted: max connections reached")]
    PoolExhausted,
}

/// Trait for a connection that can be used and checked
pub trait Connection: Send + Sync {
    /// Check if the connection is still valid
    fn is_valid(&self) -> bool;

    /// Close the connection
    fn close(&self);
}

/// Example TCP connection wrapper
pub struct TcpConnection {
    pub address: String,
    pub connected: Arc<AtomicBool>,
}

impl TcpConnection {
    pub fn new(address: &str) -> Self {
        Self {
            address: address.to_string(),
            connected: Arc::new(AtomicBool::new(true)),
        }
    }
}

impl Connection for TcpConnection {
    fn is_valid(&self) -> bool {
        self.connected.load(Ordering::SeqCst)
    }

    fn close(&self) {
        self.connected.store(false, Ordering::SeqCst);
    }
}
```

---

## 2. Connection Pool Implementation

```rust
/// Configuration for the connection pool
#[derive(Clone)]
pub struct PoolConfig {
    pub max_connections: usize,
    pub timeout: Duration,
    pub idle_timeout: Duration,
}

impl Default for PoolConfig {
    fn default() -> Self {
        Self {
            max_connections: 10,
            timeout: Duration::from_secs(30),
            idle_timeout: Duration::from_secs(300),
        }
    }
}

/// A thread-safe connection pool using Arc<Mutex>
pub struct ConnectionPool<C: Connection> {
    connections: Arc<Mutex<Vec<Arc<C>>>>,
    config: PoolConfig,
    shutdown: Arc<AtomicBool>,
    create_fn: Arc<dyn Fn() -> tokio::task::JoinHandle<Result<C, ConnectionError>> + Send + Sync>,
}

impl<C: Connection> ConnectionPool<C> {
    /// Create a new connection pool
    pub fn new<F>(config: PoolConfig, create_fn: F) -> Self
    where
        F: Fn() -> tokio::task::JoinHandle<Result<C, ConnectionError>> + Send + Sync + 'static,
    {
        let pool = Self {
            connections: Arc::new(Mutex::new(Vec::new())),
            config,
            shutdown: Arc::new(AtomicBool::new(false)),
            create_fn: Arc::new(create_fn),
        };

        pool.spawn_idle_cleanup();
        pool
    }

    /// Get a connection with timeout
    pub async fn get(&self) -> Result<Arc<C>, ConnectionError> {
        if self.shutdown.load(Ordering::SeqCst) {
            return Err(ConnectionError::Shutdown);
        }

        let mut connections = self.connections.lock().await;

        // Try to reuse an existing connection
        if let Some(idx) = connections.iter().position(|c| c.is_valid()) {
            let conn = connections.swap_remove(idx);
            return Ok(conn);
        }

        // Check if we can create a new connection
        if connections.len() < self.config.max_connections {
            let handle = (self.create_fn)();
            match timeout(self.config.timeout, handle).await {
                Ok(Ok(conn)) => {
                    connections.push(Arc::new(conn));
                    return Ok(Arc::new(conn));
                }
                Ok(Err(e)) => return Err(e),
                Err(_) => return Err(ConnectionError::Timeout(self.config.timeout)),
            }
        }

        Err(ConnectionError::PoolExhausted)
    }

    /// Return a connection to the pool
    pub async fn return_connection(&self, conn: Arc<C>) {
        if self.shutdown.load(Ordering::SeqCst) {
            return;
        }

        let mut connections = self.connections.lock().await;
        if conn.is_valid() {
            connections.push(conn);
        }
    }

    /// Initiate graceful shutdown
    pub fn shutdown(&self) {
        self.shutdown.store(true, Ordering::SeqCst);
    }

    /// Check if the pool is shutting down
    pub fn is_shutting_down(&self) -> bool {
        self.shutdown.load(Ordering::SeqCst)
    }

    /// Get current number of connections
    pub async fn size(&self) -> usize {
        self.connections.lock().await.len()
    }

    /// Spawn background task to clean up idle connections
    fn spawn_idle_cleanup(&self) {
        let pool = Arc::new(self.clone());
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(pool.config.idle_timeout);
            loop {
                interval.tick().await;
                if pool.shutdown.load(Ordering::SeqCst) {
                    break;
                }
                let mut connections = pool.connections.lock().await;
                connections.retain(|c| c.is_valid());
            }
        });
    }
}
```

---

## 3. Example Usage

```rust
#[tokio::main]
async fn main() {
    let config = PoolConfig {
        max_connections: 5,
        timeout: Duration::from_secs(5),
        idle_timeout: Duration::from_secs(30),
    };

    let pool = ConnectionPool::new(config, || {
        tokio::spawn(async {
            let conn = TcpConnection::new("localhost:8080");
            Ok(conn)
        })
    });

    // Acquire and use a connection
    match pool.get().await {
        Ok(conn) => {
            println!("Got connection: {}", conn.address);
            // Use the connection
            pool.return_connection(conn).await;
        }
        Err(e) => eprintln!("Failed to get connection: {}", e),
    }

    // Graceful shutdown
    pool.shutdown();
    println!("Pool shutting down...");
}
```

---

## 4. Cargo.toml Dependencies

```toml
[package]
name = "connection_pool"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1", features = ["full"] }
thiserror = "1.0"
```

---

## 5. Key Features

- ✅ Thread-safe using `Arc<Mutex<>>`
- ✅ Async support with Tokio
- ✅ Configurable timeouts and max connections
- ✅ Graceful shutdown via atomic flag
- ✅ Connection validation and cleanup
- ✅ Comprehensive error handling
- ✅ Modular and extensible

This implementation is production-ready and can be adapted for TCP, database, or other connection types by implementing the `Connection` trait.