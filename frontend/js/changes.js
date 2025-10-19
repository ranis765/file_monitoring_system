document.addEventListener('DOMContentLoaded', async () => {
    if (!localStorage.getItem('username')) return window.location.href = 'index.html';
    document.getElementById('logout').addEventListener('click', () => localStorage.removeItem('username'));

    try {
        const changes = await getAllChanges();
        const container = document.getElementById('changes-list');

        const grouped = changes.reduce((acc, session) => {
            const date = new Date(session.started_at).toLocaleDateString();
            if (!acc[date]) acc[date] = [];
            acc[date].push(session);
            return acc;
        }, {});

        for (const [date, sessions] of Object.entries(grouped)) {
            const section = document.createElement('section');
            section.innerHTML = `<h2>${date}</h2>`;
            sessions.forEach(session => {
                const card = document.createElement('div');
                card.classList.add('card');
                card.innerHTML = `<h3>${session.file_name} (${session.file_path})</h3><p>By: ${session.username}</p><p>Comment: ${session.comment ? session.comment.content : 'No comment'}</p><p>Type: ${session.comment ? session.comment.change_type : 'N/A'}</p>`;
                section.appendChild(card);
            });
            container.appendChild(section);
        }
    } catch (error) {
        console.error(error);
        alert('Error loading changes');
    }
}); 