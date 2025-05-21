// booking/static/booking/js/analytics_charts.js

// Top Rooms Chart
const topRoomsCtx = document.getElementById('topRoomsChart');
new Chart(topRoomsCtx, {
    type: 'bar',
    data: {
        labels: topRoomsData.map(r => r.name),
        datasets: [{
            label: 'Bookings',
            data: topRoomsData.map(r => r.bookings_count),
            backgroundColor: 'rgba(233, 241, 246, 0.7)'
        }]
    }
});

// Average Occupancy
const occCtx = document.getElementById('occupancyChart');
new Chart(occCtx, {
    type: 'bar',
    data: {
        labels: avgOccupancyData.map(r => r.name),
        datasets: [{
            label: 'Average Occupancy (%)',
            data: avgOccupancyData.map(r => (r.average_occupancy * 100).toFixed(2)),
            backgroundColor: 'rgba(236, 237, 237, 0.7)'
        }]
    }
});

// Heatmap Placeholder (You can improve this with a chart.js plugin)
const heatmapCtx = document.getElementById('heatmapChart');
new Chart(heatmapCtx, {
    type: 'scatter',
    data: {
        datasets: [{
            label: 'Booking Heatmap',
            data: heatmapData.map(h => ({
                x: h.hour,
                y: h.weekday,
                r: h.count
            })),
            backgroundColor: 'rgba(12, 12, 12, 0.5)'
        }]
    },
    options: {
        scales: {
            x: { title: { display: true, text: 'Hour of Day' } },
            y: { title: { display: true, text: 'Day of Week' }, ticks: { stepSize: 1 } }
        }
    }
});
