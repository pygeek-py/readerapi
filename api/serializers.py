from rest_framework import serializers
from django.contrib.auth.models import User
from .models import books, borrow

class userserializer(serializers.ModelSerializer):
	class Meta:
		model = User
		fields = ['id', 'username', 'email', 'password']
		extra_kwargs = {
			'password': {'write_only': True}
		}

	def create(self, validated_data):
		password = validated_data.pop('password', None)
		instance = self.Meta.model(**validated_data)
		if password is not None:
			instance.set_password(password)
		instance.save()
		return instance


class bookserializer(serializers.ModelSerializer):
	user = userserializer()
	class Meta:
		model = books
		fields = '__all__'

class borrowserializer(serializers.ModelSerializer):
    class Meta:
        model = borrow
        fields = ['id', 'user', 'title', 'description', 'genre', 'name', 'num', 'imprint', 'due']