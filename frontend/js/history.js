document.addEventListener('DOMContentLoaded', async () => {
    if (!localStorage.getItem('username')) return window.location.href = 'index.html';
    document.getElementById('logout').addEventListener('click', () => localStorage.removeItem('username'));

    try {
        const history = await getUserHistory();
        const activeList = document.getElementById('active-files');
        const recentList = document.getElementById('recent-files');

        history.active_files.forEach(file => {
            const li = document.createElement('li');
            li.innerHTML = `File: ${file.file_name} (${file.file_path})<br>Started: ${file.session_started}<br>Last: ${file.last_activity}<br>Resumes: ${file.resume_count}<br>Commented: ${file.is_commented}`;
            activeList.appendChild(li);
        });

        history.recent_files.forEach(file => {
            const li = document.createElement('li');
            li.innerHTML = `File: ${file.file_name} (${file.file_path})<br>Started: ${file.session_started}<br>Ended: ${file.session_ended}<br>Duration: ${file.session_duration} sec<br>Resumes: ${file.resume_count}<br>Commented: ${file.is_commented}`;
            recentList.appendChild(li);
        });
    } catch (error) {
        console.error(error);
        alert('Error loading history');
    }
});