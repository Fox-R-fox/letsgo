// Trading Bot Main JavaScript File

class TradingBotApp {
    constructor() {
        this.socket = io();
        this.activeBots = new Map();
        this.marketData = new Map();
        this.portfolioData = {};
        this.init();
    }

    init() {
        this.setupSocketListeners();
        this.setupEventHandlers();
        this.loadInitialData();
        this.startDataUpdates();
    }

    setupSocketListeners() {
        // Market data updates
        this.socket.on('market_data_update', (data) => {
            this.handleMarketDataUpdate(data);
        });

        // Bot status updates
        this.socket.on('bot_status_update', (data) => {
            this.handleBotStatusUpdate(data);
        });

        // Trade execution updates
        this.socket.on('trade_executed', (data) => {
            this.handleTradeUpdate(data);
        });

        // Log updates
        this.socket.on('log_update', (data) => {
            this.handleLogUpdate(data);
        });

        // Connection status
        this.socket.on('connect', () => {
            this.showNotification('Connected to trading server', 'success');
            this.updateConnectionStatus(true);
        });

        this.socket.on('disconnect', () => {
            this.showNotification('Disconnected from trading server', 'warning');
            this.updateConnectionStatus(false);
        });
    }

    setupEventHandlers() {
        // Bot launcher form
        const botForm = document.getElementById('botLauncherForm');
        if (botForm) {
            botForm.addEventListener('submit', (e) => this.handleBotLaunch(e));
        }

        // Strategy parameter changes
        const strategySelect = document.getElementById('strategy');
        if (strategySelect) {
            strategySelect.addEventListener('change', (e) => this.updateStrategyParameters(e));
        }

        // Manual refresh buttons
        const refreshMarketData = document.getElementById('refreshMarketData');
        if (refreshMarketData) {
            refreshMarketData.addEventListener('click', () => this.refreshMarketData());
        }

        const refreshPortfolio = document.getElementById('refreshPortfolio');
        if (refreshPortfolio) {
            refreshPortfolio.addEventListener('click', () => this.loadPortfolioData());
        }
    }

    async handleBotLaunch(e) {
        e.preventDefault();
        
        const formData = new FormData(e.target);
        const data = {
            instrument_type: formData.get('instrument_type'),
            strategy: formData.get('strategy'),
            trading_mode: formData.get('trading_mode'),
            capital: parseFloat(formData.get('capital')),
            symbols: formData.get('symbols') ? formData.get('symbols').split(',').map(s => s.trim()) : [],
            strategy_params: this.getStrategyParameters()
        };

        try {
            const response = await fetch('/api/start_bot', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(data)
            });

            const result = await response.json();
            
            if (result.success) {
                this.showNotification('Trading bot started successfully!', 'success');
                e.target.reset();
                this.loadActiveBots();
            } else {
                this.showNotification(`Failed to start bot: ${result.error || 'Unknown error'}`, 'error');
            }
        } catch (error) {
            this.showNotification('Network error while starting bot', 'error');
            console.error('Bot launch error:', error);
        }
    }

    handleMarketDataUpdate(data) {
        this.marketData.set(data.symbol, data);
        this.updateMarketWatchTable(data);
        this.updatePortfolioValues();
    }

    updateMarketWatchTable(data) {
        const tbody = document.getElementById('marketWatchBody');
        if (!tbody) return;

        let row = document.getElementById(`row-${data.symbol}`);
        const changeClass = data.change >= 0 ? 'market-up' : 'market-down';

        if (!row) {
            row = document.createElement('tr');
            row.id = `row-${data.symbol}`;
            row.className = 'price-update';
            row.innerHTML = `
                <td><strong>${data.symbol}</strong></td>
                <td>${data.last_price.toFixed(2)}</td>
                <td class="${changeClass}">${data.change.toFixed(2)}</td>
                <td class="${changeClass}">${data.change_percent.toFixed(2)}%</td>
                <td>${data.volume.toLocaleString()}</td>
                <td>${data.open.toFixed(2)}</td>
                <td>${data.high.toFixed(2)}</td>
                <td>${data.low.toFixed(2)}</td>
                <td>${data.close.toFixed(2)}</td>
            `;
            tbody.appendChild(row);
        } else {
            row.className = 'price-update';
            const cells = row.getElementsByTagName('td');
            cells[1].textContent = data.last_price.toFixed(2);
            cells[2].textContent = data.change.toFixed(2);
            cells[2].className = changeClass;
            cells[3].textContent = data.change_percent.toFixed(2) + '%';
            cells[3].className = changeClass;
            cells[4].textContent = data.volume.toLocaleString();
            cells[5].textContent = data.open.toFixed(2);
            cells[6].textContent = data.high.toFixed(2);
            cells[7].textContent = data.low.toFixed(2);
            cells[8].textContent = data.close.toFixed(2);
        }

        // Remove animation class after animation completes
        setTimeout(() => {
            if (row) row.className = '';
        }, 1000);
    }

    async loadInitialData() {
        await Promise.all([
            this.loadMarketStatus(),
            this.loadActiveBots(),
            this.loadPortfolioData(),
            this.loadMarketWatch()
        ]);
    }

    async loadMarketStatus() {
        try {
            const response = await fetch('/api/market_status');
            const data = await response.json();
            this.updateMarketStatusDisplay(data);
        } catch (error) {
            console.error('Error loading market status:', error);
        }
    }

    updateMarketStatusDisplay(data) {
        const statusElement = document.getElementById('marketStatus');
        const timeElement = document.getElementById('currentTime');
        
        if (statusElement) {
            if (data.is_open) {
                statusElement.innerHTML = '<span class="market-status-open">MARKET OPEN</span>';
            } else {
                statusElement.innerHTML = '<span class="market-status-closed">MARKET CLOSED</span>';
            }
        }
        
        if (timeElement) {
            timeElement.textContent = new Date().toLocaleTimeString('en-IN', { 
                timeZone: 'Asia/Kolkata',
                hour12: false 
            });
        }
    }

    async loadActiveBots() {
        try {
            const response = await fetch('/api/active_bots');
            const bots = await response.json();
            this.updateActiveBotsDisplay(bots);
        } catch (error) {
            console.error('Error loading active bots:', error);
        }
    }

    updateActiveBotsDisplay(bots) {
        const container = document.getElementById('activeBots');
        if (!container) return;

        if (bots.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-4">
                    <i class="fas fa-robot fa-3x mb-3"></i>
                    <p>No active bots</p>
                </div>
            `;
            return;
        }

        container.innerHTML = bots.map(bot => `
            <div class="card mb-3 strategy-active">
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-center">
                        <div>
                            <h6 class="card-title mb-1">${bot.strategy_name}</h6>
                            <p class="card-text mb-1">
                                <small class="text-muted">
                                    ${bot.instrument_type.toUpperCase()} • 
                                    ${bot.trading_mode.toUpperCase()} • 
                                    ₹${bot.initial_capital.toLocaleString()}
                                </small>
                            </p>
                        </div>
                        <div class="text-end">
                            <span class="bot-status-running">RUNNING</span>
                            <br>
                            <button class="btn btn-sm btn-danger mt-1" onclick="app.stopBot(${bot.id})">
                                <i class="fas fa-stop"></i> Stop
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `).join('');
    }

    async loadPortfolioData() {
        try {
            const [positionsResponse, ordersResponse] = await Promise.all([
                fetch('/api/positions'),
                fetch('/api/orders?limit=5')
            ]);

            const positions = await positionsResponse.json();
            const orders = await ordersResponse.json();

            this.updatePortfolioDisplay(positions, orders);
        } catch (error) {
            console.error('Error loading portfolio data:', error);
        }
    }

    updatePortfolioDisplay(positions, orders) {
        // Update portfolio summary
        const totalValue = positions.reduce((sum, pos) => sum + pos.invested_amount, 0) + 1000000;
        const totalPnl = positions.reduce((sum, pos) => sum + (pos.unrealized_pnl || 0), 0);

        document.getElementById('portfolioValue').textContent = this.formatCurrency(totalValue);
        document.getElementById('totalPnl').textContent = this.formatCurrency(totalPnl);
        document.getElementById('availableCash').textContent = this.formatCurrency(1000000);

        // Update positions table if exists
        const positionsTable = document.getElementById('positionsTable');
        if (positionsTable) {
            this.updatePositionsTable(positions);
        }

        // Update orders table if exists
        const ordersTable = document.getElementById('ordersTable');
        if (ordersTable) {
            this.updateOrdersTable(orders);
        }
    }

    updateStrategyParameters() {
        const strategySelect = document.getElementById('strategy');
        const paramsContainer = document.getElementById('strategyParams');
        
        if (!strategySelect || !paramsContainer) return;

        const strategy = strategySelect.value;
        let paramsHtml = '';

        switch (strategy) {
            case 'moving_average':
                paramsHtml = `
                    <div class="row">
                        <div class="col-md-6">
                            <label class="form-label">Fast Period</label>
                            <input type="number" class="form-control" name="fast_period" value="10" min="5" max="50">
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">Slow Period</label>
                            <input type="number" class="form-control" name="slow_period" value="20" min="10" max="100">
                        </div>
                    </div>
                    <div class="row mt-2">
                        <div class="col-md-12">
                            <label class="form-label">Capital per Trade (₹)</label>
                            <input type="number" class="form-control" name="capital_per_trade" value="10000" min="1000">
                        </div>
                    </div>
                `;
                break;
            case 'rsi':
                paramsHtml = `
                    <div class="row">
                        <div class="col-md-4">
                            <label class="form-label">RSI Period</label>
                            <input type="number" class="form-control" name="rsi_period" value="14" min="5" max="30">
                        </div>
                        <div class="col-md-4">
                            <label class="form-label">Oversold Level</label>
                            <input type="number" class="form-control" name="oversold" value="30" min="10" max="40">
                        </div>
                        <div class="col-md-4">
                            <label class="form-label">Overbought Level</label>
                            <input type="number" class="form-control" name="overbought" value="70" min="60" max="90">
                        </div>
                    </div>
                    <div class="row mt-2">
                        <div class="col-md-12">
                            <label class="form-label">Capital per Trade (₹)</label>
                            <input type="number" class="form-control" name="capital_per_trade" value="10000" min="1000">
                        </div>
                    </div>
                `;
                break;
        }

        paramsContainer.innerHTML = paramsHtml;
    }

    getStrategyParameters() {
        const strategy = document.getElementById('strategy').value;
        const params = {};

        switch (strategy) {
            case 'moving_average':
                params.fast_period = parseInt(document.querySelector('input[name="fast_period"]')?.value) || 10;
                params.slow_period = parseInt(document.querySelector('input[name="slow_period"]')?.value) || 20;
                params.capital_per_trade = parseInt(document.querySelector('input[name="capital_per_trade"]')?.value) || 10000;
                break;
            case 'rsi':
                params.rsi_period = parseInt(document.querySelector('input[name="rsi_period"]')?.value) || 14;
                params.oversold = parseInt(document.querySelector('input[name="oversold"]')?.value) || 30;
                params.overbought = parseInt(document.querySelector('input[name="overbought"]')?.value) || 70;
                params.capital_per_trade = parseInt(document.querySelector('input[name="capital_per_trade"]')?.value) || 10000;
                break;
        }

        return params;
    }

    async stopBot(botId) {
        try {
            const response = await fetch(`/api/stop_bot/${botId}`);
            const result = await response.json();
            
            if (result.success) {
                this.showNotification('Bot stopped successfully', 'success');
                this.loadActiveBots();
            } else {
                this.showNotification('Failed to stop bot', 'error');
            }
        } catch (error) {
            this.showNotification('Network error while stopping bot', 'error');
        }
    }

    async loadMarketWatch() {
        try {
            const response = await fetch('/api/market_watch');
            const symbols = await response.json();
            
            // Subscribe to market data via socket
            this.socket.emit('subscribe_market_data', { symbols: symbols.map(s => s.symbol) });
        } catch (error) {
            console.error('Error loading market watch:', error);
        }
    }

    updatePortfolioValues() {
        // Calculate and update portfolio values based on current market data
        const currentPrices = Object.fromEntries(this.marketData);
        // This would typically make an API call to get updated P&L
    }

    formatCurrency(amount) {
        return '₹' + amount.toLocaleString('en-IN', { 
            minimumFractionDigits: 2, 
            maximumFractionDigits: 2 
        });
    }

    showNotification(message, type = 'info') {
        // Create toast notification
        const toast = document.createElement('div');
        toast.className = `alert alert-${type} alert-dismissible fade show`;
        toast.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;

        const container = document.getElementById('notifications') || document.body;
        container.appendChild(toast);

        // Auto remove after 5 seconds
        setTimeout(() => {
            if (toast.parentNode) {
                toast.remove();
            }
        }, 5000);
    }

    updateConnectionStatus(connected) {
        const indicator = document.getElementById('connectionStatus');
        if (indicator) {
            indicator.innerHTML = connected ? 
                '<span class="badge bg-success">Connected</span>' :
                '<span class="badge bg-danger">Disconnected</span>';
        }
    }

    startDataUpdates() {
        // Update market status every second
        setInterval(() => this.loadMarketStatus(), 1000);
        
        // Update active bots every 10 seconds
        setInterval(() => this.loadActiveBots(), 10000);
        
        // Update portfolio every 30 seconds
        setInterval(() => this.loadPortfolioData(), 30000);
    }
}

// Initialize the app when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    window.app = new TradingBotApp();
});

// Utility functions
function formatIndianCurrency(num) {
    return new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        minimumFractionDigits: 2
    }).format(num);
}

function getColorForValue(value, isPercent = false) {
    if (value > 0) return 'market-up';
    if (value < 0) return 'market-down';
    return '';
}