document.addEventListener('DOMContentLoaded', async () => {
    if (!localStorage.getItem('username')) return window.location.href = 'index.html';
    document.getElementById('logout').addEventListener('click', () => localStorage.removeItem('username'));

    try {
        const sessions = await getUserSessions();
        const changeTypes = (await getChangeTypes()).change_types;
        const container = document.getElementById('sessions-list');

        sessions.forEach(session => {
            const card = document.createElement('div');
            card.classList.add('card');
            card.innerHTML = `
                <h3>File: ${session.file_name} (${session.file_path})</h3>
                <p>Started: ${new Date(session.started_at).toLocaleString()}</p>
                <p>Last Activity: ${new Date(session.last_activity).toLocaleString()}</p>
                <p>Status: ${session.ended_at ? 'Closed' : 'Open'}</p>
                <p>Commented: ${session.is_commented ? 'Yes' : 'No'}</p>
                ${session.is_commented ? '' : `
                    <select id="change-type-${session.id}">
                        ${changeTypes.map(type => `<option value="${type}">${type}</option>`).join('')}
                    </select>
                    <textarea id="comment-${session.id}" placeholder="Enter comment"></textarea>
                    <button id="comment-btn-${session.id}">Comment</button>
                `}
            `;
            container.appendChild(card);

            if (!session.is_commented) {
                document.getElementById(`comment-btn-${session.id}`).addEventListener('click', async () => {
                    const content = document.getElementById(`comment-${session.id}`).value;
                    const changeType = document.getElementById(`change-type-${session.id}`).value;
                    if (content) {
                        await postComment(session.id, content, changeType);
                        alert('Comment added!');
                        location.reload();
                    } else {
                        alert('Comment is required');
                    }
                });
            }
        });
    } catch (error) {
        console.error(error);
        alert('Error loading sessions');
    }
});