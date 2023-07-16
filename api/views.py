from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib.auth import authenticate, login, logout
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.authentication import get_authorization_header
from django.http import HttpResponseRedirect
from django.urls import reverse
from rest_framework.views import APIView
from rest_framework.exceptions import AuthenticationFailed
from django.contrib.auth.models import User
import datetime, jwt
from rest_framework.authtoken.models import Token
from rest_framework import viewsets, filters, generics, permissions
from rest_framework.parsers import MultiPartParser, FormParser
from .serializers import userserializer, bookserializer, borrowserializer
from .models import books, borrow
from rest_framework.generics import ListAPIView
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

# Create your views here.
@api_view(['POST'])
def signup(request):
    serializer = userserializer(data=request.data)
    if serializer.is_valid(raise_exception=True):
        serializer.save()
    return Response(serializer.data)

@api_view(['POST'])
def signin(request):
    username = request.data['username']
    password = request.data['password']

    print(username)
    print(password)

    user = authenticate(request, username=username, password=password)

    if user is not None:
        login(request, user)
        serializer = userserializer(user)

    #user = User.objects.filter(username=username).first()

    if user is None:
        raise AuthenticationFailed('User not found')
    if not user.check_password(password):
        raise AuthenticationFailed('Incorrect password')

    payload = {
        'id': user.id,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(minutes=60),
        'iat': datetime.datetime.utcnow()
    }

    token = jwt.encode(payload, 'secret', algorithm='HS256')

    response = Response()

    response.set_cookie(key='jwt', value=token, httponly=True)
    response.data = {
        'jwt': token,
        'id': user.id,
        'username': user.username
        }

    return response

@api_view(['GET'])
def logout(request):
	logout(request)
	response = Response()
	response.data = {
		'message': 'success'
	}
	return response

@api_view(['GET'])
def getbook(request):
    alls = books.objects.all().order_by('-id')
    serializer = bookserializer(alls, many=True)
    return Response(serializer.data)

@api_view(['POST'])
def borrows(request):
    serializer = borrowserializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
    return Response(serializer.data)

@api_view(['GET'])
def eachbook(request, pk):
    book = books.objects.get(num=pk)
    serializer = bookserializer(book, many=False)
    return Response(serializer.data)

@api_view(['GET'])
def eachbobook(request, pk):
    book = borrow.objects.filter(num=pk)
    serializer = borrowserializer(book, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def userbo(request, pk):
    use = User.objects.get(id=pk)
    book = borrow.objects.filter(user=use)
    serializer = borrowserializer(book, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def gens(request):
    alls = books.objects.filter(genre="Fiction")
    serializer = bookserializer(alls, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def gensr(request):
    alls = books.objects.filter(genre="Romance")
    serializer = bookserializer(alls, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def author(request):
    users = User.objects.all()
    serializer = userserializer(users, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def autbook(request, pk):
    uses = User.objects.get(id=pk)
    use = books.objects.filter(user=uses)
    serializer = bookserializer(use, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def userb(request, pk):
    use = User.objects.get(id=pk)
    serializer = userserializer(use, many=False)
    return Response(serializer.data)

class EventListView(ListAPIView):
    queryset = books.objects.all()
    serializer_class = bookserializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['^title']
    

@api_view(['POST'])
def bookp(request):
    title = request.data.get('title', '')
    genre = request.data.get('genre', '')
    description = request.data.get('description', '')
    num = request.data.get('num', '')
    user = request.data.get('user', '')
    name = request.data.get('name', '')

    use = User.objects.get(username=user)

    eve = books(user=use, title=title, genre=genre, description=description, num=num, name=name)

    eve.save()

    return Response({'message': 'Upload Successfully'})