# ... (весь код до функции createChannel остаётся тем же)

function createChannel() {
    const nameInput = document.getElementById('channel-name');
    const descriptionInput = document.getElementById('channel-description');
    
    const display_name = nameInput.value.trim();
    const description = descriptionInput.value.trim();
    
    if (!display_name) {
        alert('Введите название канала');
        return;
    }

    // Формируем "name" в нижнем регистре, только буквы, цифры и подчёркивание
    const name = display_name.toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');

    if (name.length < 2) {
        alert('Название канала должно содержать хотя бы 2 допустимых символа');
        return;
    }

    fetch('/create_channel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            name: name,
            display_name: display_name,
            description: description,
            is_private: false
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            // Успех
            alert('Канал успешно создан!');
            closeModal('create-channel-modal');

            // Очищаем поля
            nameInput.value = '';
            descriptionInput.value = '';

            // Обновляем список каналов в сайдбаре
            loadChannels();

            // Сразу открываем созданный канал
            openChat(data.channel_name, 'channel', data.display_name);
        } else {
            alert(data.error || 'Ошибка при создании канала');
        }
    })
    .catch(err => {
        console.error(err);
        alert('Ошибка соединения с сервером');
    });
}
