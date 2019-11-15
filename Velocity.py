'''
PyTrx (c) by Penelope How, Nick Hulton, Lynne Buie

PyTrx is licensed under a
Creative Commons Attribution 4.0 International License.

You should have received a copy of the license along with this
work. If not, see <http://creativecommons.org/licenses/by/4.0/>.


PYTRX VELOCITY MODULE

This script is part of PyTrx, an object-oriented programme created for the 
purpose of calculating real-world measurements from oblique images and 
time-lapse image series.

This is the Velocity module of PyTrx. It handles the functionality for 
obtaining velocity and homography  measurements from oblique time-lapse imagery. 
Specifically, this module contains functions for:
(1) Performing camera registration from static point feature tracking (referred 
    to here as homography).
(2) Calculating surface velocities derived from feature tracking, with 
    associated errors and signal-to-noise ratio calculated.
(3) Determining real-world surface areas and distances from oblique imagery.

Classes
Velocity:                       A class for the processing of an image sequence 
                                to determine pixel displacements and real-world 
                                velocities from a sparse set of points
Homography:                     A class for determining camera platform motion 
                                through an image sequence

Key Velocity functions 
calcVelocities:                 Calculate velocities between succesive image 
                                pairs in an image sequence

Key Homography functions
calcHomographyPairs:            Calculate homography between succesive image 
                                pairs in an image sequence
                               
Key standalone functions
calcVelocity:                   Calculate velocities between an image pair
calcHomography:                 Calculate homography between an image pair
'''

#Import packages
import numpy as np
import cv2
import math
from matplotlib import path
import matplotlib.pyplot as plt
from scipy import interpolate
from PIL import Image, ImageDraw
import ogr
import numpy.ma as ma

#Import PyTrx functions and classes
from FileHandler import readMask
from Images import ImageSequence
from CamEnv import projectUV, projectXYZ, setProjection

#------------------------------------------------------------------------------
class Homography(ImageSequence):
    '''A class for the processing the homography of an image sequence to 
    determine motion in a camera platform.
    
    This class treats the images as a contigous sequence of name references by
    default.
    
    Args
    imageList:          List of images, for the ImageSet object.
    camEnv:             The Camera Environment corresponding to the images, 
                        for the ImageSequence object.
    invmaskPath:        As above, but the mask for the stationary feature 
                        tracking (for camera registration/determining
                        camera homography).
    band:               String denoting the desired image band.
    equal:              Flag denoting whether histogram equalisation is applied 
                        to images (histogram equalisation is applied if True). 
                        Default is True.                        
    '''
        
    def __init__(self, imageList, camEnv, invmaskPath=None, calibFlag=True, 
                 band='L', equal=True):
        
        ImageSequence.__init__(self, imageList, band, equal)
        
        #Set initial class properties
        self._camEnv = camEnv
        self._imageN = self.getLength()-1
        self._calibFlag = calibFlag
         
        #Set mask
        if invmaskPath is None:
            self._invmask = None
        else:
            self._invmask = readMask(self.getImageArrNo(0), invmaskPath)
            print('\nHomography mask set')


    def calcHomographies(self, winsize=(25,25), back_thresh=1.0, 
                         min_features=4, seedparams=[50000, 0.1, 5.0]):
        '''Function to generate a homography model through a sequence of 
        images, and perform for image registration. Points that are assumed 
        to be static in the image plane are tracked between image pairs, and 
        movement in these points are used to generate sequential homography 
        models.
        
        The homography models are held in the Velocity object and can be called
        in subsequent velocity functions, such as calcVelocities and
        calcVelocity.
        
        Inputs
        back_thesh:                 Threshold for back-tracking distance (i.e.
                                    the difference between the original seeded
                                    point and the back-tracked point in im0).
        maxpoints:                  Maximum number of points to seed in im0
        quality:                    Corner feature quality.
        mindist:                    Minimum distance between seeded points.                 
        min_features:               Minimum number of seeded points to track.
        ''' 
        print('\n\nCALCULATING HOMOGRAPHY')
        
        #Create empty list for outputs
        homog=[]   
        
        #Get first image (image0) path and array data
        imn1=self._imageSet[0].getImageName()
        im1=self._imageSet[0].getImageArray()
        
        #Cycle through image pairs (numbered from 0)
        for i in range(self.getLength()-1):
            
            #Re-assign first image in image pair
            im0=im1
            imn0=imn1
            
            #Get second image in image pair (clear memory subsequently)
            im1=self._imageSet[i+1].getImageArray()
            imn1=self._imageSet[i+1].getImageName()
            self._imageSet[i].clearImage()
            self._imageSet[i].clearImageArray()
            
            print('\nProcessing homograpy for images: ' + str(imn0) + ' and ' 
                  + str(imn1))
            
            #Get inverse mask and calibration parameters
            invmask = self.getInverseMask()
            cameraMatrix=self._camEnv.getCamMatrixCV2()
            distortP=self._camEnv.getDistortCoeffsCV2()
               
            #Calculate homography and errors from image pair
            hg=calcSparseHomography(im0, im1, invmask, [cameraMatrix, distortP], 
                                    method=cv2.RANSAC,
                                    ransacReprojThreshold=5.0,
                                    winsize=winsize,
                                    back_thresh=back_thresh,
                                    min_features=min_features,
                                    seedparams=[seedparams[0],
                                               seedparams[1],
                                               seedparams[2]])
        
            #Assign homography information as object attributes
            homog.append(hg)
            
        return homog            


    def getInverseMask(self):
        '''Return inverse mask.'''
        return self._invmask

            
class Velocity(ImageSequence):
    '''A class for the processing of an ImageSet to determine pixel 
    displacements and real-world velocities from a sparse set of points, with 
    methods to track in the xy image plane and project tracks to real-world 
    (xyz) coordinates.
    
    This class treats the images as a contigous sequence of name references by
    default.
    
    Args
    imageList:          List of images, for the ImageSet object.
    camEnv:             The Camera Environment corresponding to the images, 
                        for the ImageSequence object.
    maskPath:           The file path for the mask indicating the target area
                        for deriving velocities from. If this file exists, the 
                        mask will be loaded. If this file does not exist, then 
                        the mask generation process will load, and the result 
                        will be saved with this path.
    invmaskPath:        As above, but the mask for the stationary feature 
                        tracking (for camera registration/determining
                        camera homography).
    image0:             The image number in the ImageSet from which the 
                        analysis will commence. This is set to the first image 
                        in the ImageSet by default.
    band:               String denoting the desired image band.
    equal:              Flag denoting whether histogram equalisation is applied 
                        to images (histogram equalisation is applied if True). 
                        Default is True.                        
    loadall:            Flag which, if true, will force all images in the 
                        sequence to be loaded as images (array) initially and 
                        thus not re-loaded in subsequent processing. This is 
                        only advised for small image sequences. 
    timingMethod:       Method for deriving timings from imagery. By default, 
                        timings are extracted from the image EXIF data. 
    '''
        
    def __init__(self, imageList, camEnv, homography=None, maskPath=None, 
                 calibFlag=True, band='L', equal=True):
        
        ImageSequence.__init__(self, imageList, band, equal)
        
        #Set initial class properties
        self._camEnv = camEnv
        self._homog = homography
        self._imageN = self.getLength()-1
        self._calibFlag = calibFlag
        
        #Set mask 
        if maskPath is None:
            self._mask = None
        else:
            self._mask = readMask(self.getImageArrNo(0), maskPath)
            print('\nVelocity mask set')


    def calcVelocities(self, params):
        '''Function to calculate velocities between succesive image pairs. 
        Image pairs are called from the ImageSequence object. Points are seeded
        in the first of these pairs using the Shi-Tomasi algorithm with 
        OpenCV's goodFeaturesToTrack function. 
        
        The Lucas Kanade optical flow algorithm is applied using the OpenCV 
        function calcOpticalFlowPyrLK to find these tracked points in the 
        second image of each image pair. A backward tracking method then tracks 
        back from these to the first image in the pair, checking if this is 
        within a certain distance as a validation measure.
        
        Tracked points are corrected for image distortion and camera platform
        motion (if needed). The points in each image pair are georectified 
        subsequently to obtain xyz points. The georectification functions are 
        called from the Camera Environment object, and are based on those in
        ImGRAFT (Messerli and Grinsted, 2015). Velocities are finally derived 
        from these using a simple Pythagoras' theorem method.
        
        This function returns the xyz velocities and points from each image 
        pair, and their corresponding uv velocities and points in the image 
        plane.
        
        Args
        params (list):              List that defines the parameters for 
                                    deriving velocity:
                                        
                                    Method: 'sparse' or 'dense' (str).
                                    
                                    Seed parameters: either containing the 
                                    corner parameters for the sparse method 
                                    - max. number of corners (int), quality 
                                    (int), and min. distance (int). Or the grid 
                                    spacing (list) for the dense method.
                                    
                                    Tracking parameters: either containing the
                                    sparse method parameters -
                                    window size (tuple), backtracking threshold 
                                    (int) and minimum tracked features (int).
                                    Or the dense method parameters - tracking 
                                    method (int), template size (int), search
                                    window size (int), and minimum tracked 
                                    features (int)
        
        Returns
        velocity (list):            List containing the xyz and uv velocities. 
                                    The first element holds the xyz velocity 
                                    for each point (xyz[0]), the xyz positions 
                                    for the points in the first image (xyz[1]), 
                                    and the xyz positions for the points in the 
                                    second image(xyz[2]). The second element 
                                    contains the uv velocities for each point 
                                    (uv[0], the uv positions for the points in 
                                    the first image (uv[1]), the uv positions 
                                    for the points in the second image (uv[2]), 
                                    and the corrected uv points in the second 
                                    image if they have been calculated using 
                                    the homography model for image registration 
                                    (uv[3]). If the corrected points have not 
                                    been calculated then an empty list is 
                                    merely returned. 
                                    
        Input example:
        For sparse velocities: 
        velocity = calcVelocities(params=['sparse', 
                                          seedparams=[50000, 0.1, 5.0], 
                                          trackparams=[(25,25), back_thresh=1.0, 
                                          min_features=4]])
        For dense velocities:
        velocity = calcVelocities(params=['dense', 
                                          seedparams=[100,100], 
                                          trackparams=['cv2.TM_CCORR_NORMED', 
                                          templatesize=10, searchsize=30, 
                                          back_thresh=1.0, min_features=4]])
        '''
           
        print('\n\nCALCULATING VELOCITIES')
        velocity=[]

        #Get camera environment 
        camenv = self.getCamEnv()
        
        #Get DEM from camera environment
        dem = camenv.getDEM() 

        #Get projection and inverse projection variables through camera info
        projvars = [camenv._camloc, camenv._camDirection, camenv._radCorr, 
                    camenv._tanCorr, camenv._focLen, camenv._camCen, 
                    camenv._refImage]
        
        #Get inverse projection variables through camera info               
        invprojvars = setProjection(dem, camenv._camloc, camenv._camDirection, 
                                    camenv._radCorr, camenv._tanCorr, 
                                    camenv._focLen, camenv._camCen, 
                                    camenv._refImage) 
        
        #Get camera matrix and distortion parameters for calibration
        mtx=self._camEnv.getCamMatrixCV2()
        distort=self._camEnv.getDistortCoeffsCV2()
        
        #Get mask
        mask=self.getMask()
        
        #Get first image (image0) file path and array data for initial tracking
        imn1=self._imageSet[0].getImageName()
        im1=self._imageSet[0].getImageArray()
        
        #Cycle through image pairs (numbered from 0)
        for i in range(self.getLength()-1):

            #Re-assign first image in image pair
            im0=im1
            imn0=imn1
                            
            #Get second image in image pair (and subsequently clear memory)
            im1=self._imageSet[i+1].getImageArray()
            imn1=self._imageSet[i+1].getImageName()       
            self._imageSet[i].clearAll()
           
            print('\nFeature-tracking for images: ' + str(imn0) +' and ' 
                  + str(imn1))        

            #Calculate velocities between image pair with homography
            if self._homog is not None:
                if params[0]=='sparse':
                    pts=calcSparseVelocity(im0, im1, mask, [mtx,distort], 
                                           [self._homog[i][0], 
                                           self._homog[i][3]], 
                                           invprojvars, params[2][0], 
                                           params[2][1], params[2][2], 
                                           [params[1][0], params[1][1], 
                                           params[1][2]]) 
                    
                elif params[0]=='dense':
                    pts=calcDenseVelocity(im0, im1, params[1], params[2][0],
                                          params[2][1], params[2][2],
                                          mask, [mtx,distort], 
                                          [self._homog[i][0], 
                                          self._homog[i][3]], [dem, projvars, 
                                          invprojvars], params[2][3], 
                                          params[2][4])
                       
            else:
                if params[0]=='sparse':
                    pts=calcSparseVelocity(im0, im1, mask, [mtx,distort], 
                                           [None, None], invprojvars, 
                                           params[2][0], params[2][1], 
                                           params[2][2], [params[1][0], 
                                           params[1][1], params[1][2]])
                        
                elif params[0]=='dense':
                    pts=calcDenseVelocity(im0, im1, params[1], params[2][0],
                                          params[2][1], params[2][2],
                                          mask, [mtx,distort], [None, None], 
                                          [dem, projvars, invprojvars], 
                                          params[2][3], params[2][4])                     
                                                 
            #Append output
            velocity.append(pts)         
        
        #Return XYZ and UV velocity information
        return velocity
       
        
    def getMask(self):
        '''Return image mask.'''
        return self._mask
 
 
    def getCamEnv(self):
        '''Return camera environment object (CamEnv).'''
        return self._camEnv
    

#------------------------------------------------------------------------------    

def calcSparseVelocity(img1, img2, mask, calib=None, homog=None, 
                       invprojvars=None, winsize=(25,25), back_thresh=1.0, 
                       min_features=4, seedparams=[50000, 0.1, 5.0]):
    '''Function to calculate the velocity between a pair of images. Points 
    are seeded in the first of these either by a defined grid spacing, or using 
    the Shi-Tomasi algorithm with OpenCV's goodFeaturesToTrack function. 
    
    The Lucas Kanade optical flow algorithm is applied using the OpenCV 
    function calcOpticalFlowPyrLK to find these tracked points in the 
    second image. A backward tracking method then tracks back from these to 
    the original points, checking if this is within a certain distance as a 
    validation measure.
    
    Tracked points are corrected for image distortion and camera platform
    motion (if needed). The points in the image pair are georectified 
    subsequently to obtain xyz points.  The georectification functions are 
    called from the Camera Environment object, and are based on those in
    ImGRAFT (Messerli and Grinsted, 2015). Velocities are finally derived
    from these using a simple Pythagoras' theorem method.
    
    This function returns the xyz velocities and points, and their 
    corresponding uv velocities and points in the image plane.
    
    Args
    img1 (arr):                 Image 1 in the image pair.
    img2 (arr):                 Image 2 in the image pair.
    hmatrix (arr):              Homography matrix.
    hpts (arr):                 Homography points.
    back_thesh (int):           Threshold for back-tracking distance (i.e.
                                the difference between the original seeded
                                point and the back-tracked point in im0).
    min_features (int):         Minimum number of seeded points to track.
    seedparams (list):          Point seeding parameters, which indicate
                                whether points are generated based on corner
                                features or a grid with defined spacing. 
                                The three corner features parameters denote 
                                maximum number of corners detected, corner
                                quality, and minimum distance between corners; 
                                inputted as a list. For grid generation, the
                                only input parameter needed is the grid 
                                spacing; inputted as a list containing the 
                                horizontal and vertical grid spacing.
                 
    Returns
    xyz (list)                  List containing the xyz velocities for each 
                                point (xyz[0]), the xyz positions for the 
                                points in the first image (xyz[1]), and the 
                                xyz positions for the points in the second 
                                image(xyz[2]). 
    uv (list):                  List containing the uv velocities for each
                                point (uv[0], the uv positions for the 
                                points in the first image (uv[1]), the
                                uv positions for the points in the second
                                image (uv[2]), and the corrected uv points 
                                in the second image if they have been 
                                calculated using the homography model for
                                image registration (uv[3]). If the 
                                corrected points have not been calculated 
                                then an empty list is merely returned.                                 
    '''       
    #Set threshold difference for point tracks
    displacement_tolerance_rel=2.0
    
    #Seed features
    p0 = seedCorners(img1, mask, seedparams[0], seedparams[1], 
                     seedparams[2], min_features)
    
    #Track points between the image pair
    points, ptserrors = featureTrack(img1, img2, p0, winsize, back_thresh,  
                                     min_features) 
 
    #Pass empty object if tracking was insufficient
    if points==None:
        print('\nNo features to undertake velocity measurements')
        return None        
        
    if calib is not None:        
        #Calculate optimal camera matrix 
        size=img1.shape
        h = size[0]
        w = size[1]
        newMat, roi = cv2.getOptimalNewCameraMatrix(calib[0], 
                                                    calib[1], 
                                                    (w,h), 1, (w,h))
        
        #Correct tracked points for image distortion. The displacement here 
        #is defined forwards (i.e. the points in image 1 are first 
        #corrected, followed by those in image 2)      
        #Correct points in first image 
        src_pts_corr=cv2.undistortPoints(points[0],calib[0],calib[1],P=newMat)
        
        #Correct points in second image                                         
        dst_pts_corr=cv2.undistortPoints(points[1],calib[0],calib[1],P=newMat)
        
        back_pts_corr=cv2.undistortPoints(points[2],calib[0],calib[1],P=newMat)

    else:
        src_pts_corr = points[0]
        dst_pts_corr = points[1]
        back_pts_corr = points[2]

    #Calculate homography-corrected pts if desired
    if homog is not None:
        
        #Get homography matrix and homography points
        hmatrix=homog[0]
        hpts=homog[1]
        
        #Apply perspective homography matrix to tracked points
        dst_pts_homog = apply_persp_homographyPts(dst_pts_corr, hmatrix,
                                                  inverse=True)
        
        #Calculate difference between points corrected for homography and
        #those uncorrected for homography
        dispx=dst_pts_homog[:,0,0]-src_pts_corr[:,0,0]
        dispy=dst_pts_homog[:,0,1]-src_pts_corr[:,0,1]
        
        #Use pythagoras' theorem to obtain distance
        disp_dist=np.sqrt(dispx*dispx+dispy*dispy)
        
        #Determine threshold for good points using a given displacement 
        #tolerance (defined earlier)
        xsd=hpts[0][2]
        ysd=hpts[0][3]
        sderr=math.sqrt(xsd*xsd+ysd*ysd)
        good=disp_dist > sderr * displacement_tolerance_rel
        
        #Keep good points
        src_pts_corr=src_pts_corr[good]
        dst_pts_corr=dst_pts_corr[good]
        dst_pts_homog=dst_pts_homog[good]
        back_pts_corr=back_pts_corr[good]
        ptserrors=ptserrors[good]
        
        print(str(dst_pts_corr.shape[0]) + 
              ' points remaining after homography correction')

    else:
        #Original tracked points assigned if homography not given
        print('Homography matrix not supplied. Original tracked points kept')
        dst_pts_homog=dst_pts_corr
    
    #Calculate pixel velocity
    pxvel=[]       
    for c,d in zip(src_pts_corr, dst_pts_homog):                        
        pxvel.append(np.sqrt((d[0][0]-c[0][0])*(d[0][0]-c[0][0])+
                     (d[0][1]-c[0][1])*(d[0][1]-c[0][1])))
        
    #Project good points (original and tracked) to obtain XYZ coordinates
    if invprojvars is not None:        
        #Project good points from image0
        uvs=src_pts_corr[:,0,:]
        xyzs=projectUV(uvs, invprojvars)
        
        #Project good points from image1
        uvd=dst_pts_homog[:,0,:]
        xyzd=projectUV(uvd, invprojvars)

        #Project good points from image0 back-tracked
        uvb=back_pts_corr[:,0,:]
        xyzb=projectUV(uvb, invprojvars)
        
        #Calculate xyz velocity
        xyzvel=[]
        for a,b in zip(xyzs, xyzd):                        
            xyzvel.append(np.sqrt((b[0]-a[0])*(b[0]-a[0])+
                         (b[1]-a[1])*(b[1]-a[1])))
        
        #Calculate xyz error
        xyzerr=[]
        for a,b in zip(xyzs, xyzb):
            xyzerr.append(np.sqrt((b[0]-a[0])*(b[0]-a[0])+
                         (b[1]-a[1])*(b[1]-a[1])))
    else:
        xyzs=None
        xyzd=None
        xyzvel=None
        xyzerr=None
            
    #Return real-world point positions (original and tracked points),
    #and xy pixel positions (original, tracked, and homography-corrected)
    if homog is not None:
        return [[xyzvel, xyzs, xyzd, xyzerr], 
                [pxvel, src_pts_corr, dst_pts_corr, dst_pts_homog, ptserrors]]
    
    else:
        return [[xyzvel, xyzs, xyzd, xyzerr], 
                [pxvel, src_pts_corr, dst_pts_corr, None, ptserrors]]
        

def calcDenseVelocity(im0, im1, griddistance, method, templatesize, 
                      searchsize, mask, calib=None, homog=None, campars=None, 
                      back_thresh=1.0, min_features=4):
    '''Function to calculate the velocity between a pair of images using 
    a gridded template matching approach. Gridded points are defined by grid 
    distance, which are then used to either generate templates for matching
    or tracked using the Lucas Kanade optical flow algorithm.
    
    Tracked points are corrected for image distortion and camera platform
    motion (if needed). The points in the image pair are georectified 
    subsequently to obtain xyz points.  The georectification functions are 
    called from the Camera Environment object, and are based on those in
    ImGRAFT (Messerli and Grinsted, 2015). Velocities are finally derived
    from these using a simple Pythagoras' theorem method.
    
    This function returns the xyz velocities and points, and their 
    corresponding uv velocities and points in the image plane.
    
    Inputs
    im0 (arr):                  Image 1 in the image pair.
    im1 (arr):                  Image 2 in the image pair.
    griddistance (list):        Grid spacing, defined by two values. 
                                representing pixel row and column spacing.
    method (str/int):           Method for matching:
                                'opticalflow': Lucas Kanade Optical Flow.
                                cv2.TM_CCOEFF: Cross-coefficient.
                                cv2.TM_CCOEFF_NORMED: Normalised cross-coeff.
                                cv2.TM_CCORR - Cross correlation.
                                cv2.TM_CCORR_NORMED - Normalised cross-corr.
                                cv2.TM_SQDIFF - Square difference.
                                cv2.TM_SQDIFF_NORMED - Normalised square diff.
    templatesize (int):         Template window size in im0 for matching.
    searchsize (int):           Search window size in im1 for matching.                 
    mask (arr):                 Mask array for masking DEM.
    calib (list):               Calibration parameters.
    homog (list):               Homography parameters, hmatrix (arr) and hpts
                                (arr).
    campars (list):             List containing information for transforming
                                between the image plane and 3D scene:
                                1. DEM (ExplicitRaster object);
                                2. Projection parameters (camera location, 
                                camera post, radial distortion coefficients, 
                                tangential distortion coefficients, 
                                focal length, camera centre, and reference 
                                image)
                                3. Inverse projection parameters (coordinate
                                system  3D scene - X, Y, Z, uv0)         
    back_thesh (int):           Threshold for back-tracking distance (i.e.
                                the difference between the original seeded
                                point and the back-tracked point in im0).
    min_features (int):         Minimum number of seeded points to track.
                 
    Outputs
    xyz (list)                  List containing the xyz velocities for each 
                                point (xyz[0]), the xyz positions for the 
                                points in the first image (xyz[1]), and the 
                                xyz positions for the points in the second 
                                image(xyz[2]). 
    uv (list):                  List containing the uv velocities for each
                                point (uv[0], the uv positions for the 
                                points in the first image (uv[1]), the
                                uv positions for the points in the second
                                image (uv[2]), and the corrected uv points 
                                in the second image if they have been 
                                calculated using the homography model for
                                image registration (uv[3]). If the 
                                corrected points have not been calculated 
                                then an empty list is merely returned.                                 
    '''       
    #Set threshold difference for point tracks
    displacement_tolerance_rel=2.0
    
    #Seed point grid
    xyz0, uv0 = seedGrid(campars[0], griddistance, campars[1], mask)
    
    #Template match if method flag is not optical flow
    if method != 'opticalflow':
        pts, ptserrors = templateMatch(im0, im1, uv0, templatesize, searchsize, 
                                       min_features, method)
        
    #Optical Flow method if method flag is optical flow
    else:                            
        pts, ptserrors = featureTrack(im0, im1, uv0, (searchsize,searchsize), 
                                      back_thresh, min_features)
 
    #Pass empty object if tracking was insufficient
    if pts==None:
        print('\nNo features to undertake velocity measurements')
        return None        
    
    #Correct point tracks for camera distortion    
    if calib is not None: 
        
        #Calculate optimal camera matrix 
        size=im0.shape
        h = size[0]
        w = size[1]
        newMat, roi = cv2.getOptimalNewCameraMatrix(calib[0], 
                                                    calib[1], 
                                                    (w,h), 1, (w,h))
        
        #Correct tracked points for image distortion. The displacement here 
        #is defined forwards (i.e. the points in image 1 are first 
        #corrected, followed by those in image 2)      
        #Correct points in first image 
        src_pts_corr=cv2.undistortPoints(pts[0], 
                                         calib[0], 
                                         calib[1],P=newMat)
        
        #Correct points in second image                                         
        dst_pts_corr=cv2.undistortPoints(pts[1], 
                                         calib[0], 
                                         calib[1],P=newMat)
        
        #Correct back-tracked points in first image, if calculated
        if pts[2] != None:
            back_pts_corr=cv2.undistortPoints(pts[2],
                                              calib[0],
                                              calib[1],P=newMat)
        else:
            back_pts_corr = None
    
    #Return uncorrected points if calibration not given        
    else:
        src_pts_corr = pts[0]
        dst_pts_corr = pts[1]
        back_pts_corr = pts[2]

    #Calculate homography-corrected pts if desired
    if homog is not None:
        
        #Get homography matrix and homography points
        hmatrix=homog[0]
        hpts=homog[1]
        
        #Apply perspective homography matrix to tracked points
        dst_pts_homog = apply_persp_homographyPts(dst_pts_corr,
                                                  hmatrix,
                                                  inverse=True)
        
        #Calculate difference between points corrected for homography and
        #those uncorrected for homography
        dispx=dst_pts_homog[:,0,0]-src_pts_corr[:,0,0]
        dispy=dst_pts_homog[:,0,1]-src_pts_corr[:,0,1]
        
        #Use pythagoras' theorem to obtain distance
        disp_dist=np.sqrt(dispx*dispx+dispy*dispy)
        
        #Determine threshold for good points using a given displacement 
        #tolerance (defined earlier)
        xsd=hpts[0][2]
        ysd=hpts[0][3]
        sderr=math.sqrt(xsd*xsd+ysd*ysd)
        good=disp_dist > sderr * displacement_tolerance_rel
        
        #Keep good points
        src_pts_corr=src_pts_corr[good]
        dst_pts_corr=dst_pts_corr[good]
        dst_pts_homog=dst_pts_homog[good]
        if back_pts_corr != None:
            back_pts_corr=back_pts_corr[good]
        ptserrors=ptserrors[good]            
        
        print(str(dst_pts_corr.shape[0]) + 
              ' points remaining after homography correction')

    else:
        #Original tracked points assigned if homography not given
        print('Homography matrix not supplied. Original tracked points kept')
        dst_pts_homog=dst_pts_corr
    
    #Calculate pixel velocity
    pxvel=[]       
    for c,d in zip(src_pts_corr, dst_pts_homog):                        
        pxvel.append(np.sqrt((d[0][0]-c[0][0])*(d[0][0]-c[0][0])+
                     (d[0][1]-c[0][1])*(d[0][1]-c[0][1])))
        
    #Project good points (original, tracked and back-tracked) to obtain XYZ 
    #coordinates    
    if campars[2] is not None:
        
        #Project good points from image0
        uvs=src_pts_corr[:,0,:]
        xyzs=projectUV(uvs, campars[2])
        
        #Project good points from image1
        uvd=dst_pts_homog[:,0,:]
        xyzd=projectUV(uvd, campars[2])

        #Project good points from image0 back-tracked
        if back_pts_corr != None:
            uvb=back_pts_corr[:,0,:]
            xyzb=projectUV(uvb, campars[2])
        else:
            xyzb=None
            
        #Calculate xyz velocity
        xyzvel=[]
        for a,b in zip(xyzs, xyzd):                        
            xyzvel.append(np.sqrt((b[0]-a[0])*(b[0]-a[0])+
                         (b[1]-a[1])*(b[1]-a[1])))
        
        #Calculate xyz error
        if method == 'opticalflow':
            xyzerr=[]
            for a,b in zip(xyzs, xyzb):
                xyzerr.append(np.sqrt((b[0]-a[0])*(b[0]-a[0])+
                             (b[1]-a[1])*(b[1]-a[1])))
        else:
            xyzerr=None
            
    else:
        xyzs=None
        xyzd=None
        xyzvel=None
        xyzerr=None
            
    #Return real-world point positions (original and tracked points),
    #and xy pixel positions (original, tracked, and homography-corrected)
    if homog is not None:
        return [[xyzvel, xyzs, xyzd, xyzerr], 
                [pxvel, src_pts_corr, dst_pts_corr, dst_pts_homog, ptserrors]]
    
    else:
        return [[xyzvel, xyzs, xyzd, xyzerr], 
                [pxvel, src_pts_corr, dst_pts_corr, None, ptserrors]]

        
def calcHomography(img1, img2, mask, correct, method=cv2.RANSAC, 
                   ransacReprojThreshold=5.0, winsize=(25,25), 
                   back_thresh=1.0, min_features=4, seedparams=[50000, 0.1, 5.0]):
    '''Function to supplement correction for movement in the camera 
    platform given an image pair (i.e. image registration). Returns the 
    homography representing tracked image movement, and the tracked 
    features from each image.
    
    Inputs
    img1:                       Image 1 in the image pair.
    img2:                       Image 2 in the image pair.
    method:                     Method used to calculate homography model,
                                which plugs into the OpenCV function
                                cv2.findHomography: 
                                cv2.RANSAC: RANSAC-based robust method.
                                cv2.LEAST_MEDIAN: Least-Median robust 
                                0: a regular method using all the points.                                   
    ransacReprjThreshold:       Maximum allowed reprojection error.
    back_thesh:                 Threshold for back-tracking distance (i.e.
                                the difference between the original seeded
                                point and the back-tracked point in im0).
    maxpoints:                  Maximum number of points to seed in im0
    quality:                    Corner feature quality.
    mindist:                    Minimum distance between seeded points.
    calcHomogError:             Flag to denote whether homography errors
                                should be calculated.                 
    min_features:               Minimum number of seeded points to track.
    
    Outputs
    homogMatrix:                The calculated homographic shift for the 
                                image pair (homogMatrix).
    src_pts_corr,
    dst_pts_corr,
    homog_pts:                  The original, tracked and back-tracked 
                                homography points.  
    ptserror:                   Difference between the original homography 
                                points and the back-tracked points.
    homogerror:                 Difference between the interpolated 
                                homography matrix and the equivalent 
                                tracked points
    ''' 
#    [img, img2, homogparams=[method, ransacReprojThreshold], seedingparams=
#     [method, mask, correct, seedingparams], trackingparams=[trackingparams]]
    
    #If tracking method defined as sparse
    if trackingparams[0]=='sparse':
        
        #Seed corner features
        p0 = seedCorners(img1, mask, seedparams[0], seedparams[1], seedparams[2], 
                         min_features)
            
        #Feature track between images
        points, ptserrors = featureTrack(img1, img2, p0, winsize, back_thresh, 
                                          min_features) 

    #If tracking method defined as dense
    elif trackingparams[1]=='dense':
        
    #Pass empty object if tracking insufficient
    if points==None:
        print('\nNo features to undertake Homography')
        return None
    
    if correct is not None:
        
        #Calculate optimal camera matrix 
        size=img1.shape
        h = size[0]
        w = size[1]
        newMat, roi = cv2.getOptimalNewCameraMatrix(correct[0], 
                                                    correct[1], 
                                                    (w,h), 1, (w,h))
               
        #Correct tracked points for image distortion. The homgraphy here is 
        #defined forwards (i.e. the points in image 1 are first corrected, 
        #followed by those in image 2)        
        #Correct points in first image  
        src_pts_corr=cv2.undistortPoints(points[0], 
                                         correct[0], 
                                         correct[1],P=newMat)
        
        #Correct tracked points in second image
        dst_pts_corr=cv2.undistortPoints(points[1], 
                                         correct[0], 
                                         correct[1],P=newMat) 
    else:
        src_pts_corr = points[0]
        dst_pts_corr = points[1]
    
    #Find the homography between the two sets of corrected points
    homogMatrix, mask = cv2.findHomography(src_pts_corr, dst_pts_corr, 
                                           method, ransacReprojThreshold)
    
    #Calculate homography error
    #Apply global homography to source points
    homog_pts = apply_persp_homographyPts(src_pts_corr, homogMatrix, False)          

    #Calculate offsets between tracked points and the modelled points 
    #using the global homography
    xd=dst_pts_corr[:,0,0]-homog_pts[:,0,0]
    yd=dst_pts_corr[:,0,1]-homog_pts[:,0,1]
    
    #Calculate mean magnitude and standard deviations of the model 
    #homography (i.e. actual point errors)          
    xmean=np.mean(xd)       
    ymean=np.mean(yd)       #Mean should approximate to zero
    xsd=np.std(xd)          
    ysd=np.std(yd)          #SD indicates overall scale of error

    #Compile all error measures    
    homogerrors=([xmean,ymean,xsd,ysd],[xd,yd])
                
    return (homogMatrix, [src_pts_corr,dst_pts_corr,homog_pts], ptserrors, 
            homogerrors)


def apply_persp_homographyPts(pts, homog, inverse=False):        
    '''Funtion to apply a perspective homography to a sequence of 2D 
    values held in X and Y. The perspective homography is represented as a 
    3 X 3 matrix (homog). The source points are inputted as an array. The 
    homography perspective matrix is modelled in the same manner as done so 
    in OpenCV.
    
    Variables
    pts (arr/list):             Input point positions to correct
    homog (arr):                Perspective homography matrix                                  
    inverse (bool):             Flag to denote if perspective homography matrix 
                                needs inversing
    
    Returns
    hpts (arr):                 Corrected point positions
    '''         
    if isinstance(pts,np.ndarray):
        n=pts.shape[0]
        hpts=np.zeros(pts.shape)
       
        if inverse:
           val,homog=cv2.invert(homog)       
        
        for i in range(n):
            div=1./(homog[2][0]*pts[i][0][0] + homog[2][1]*pts[i][0][1] + 
                    homog[2][2])
            hpts[i][0][0]=((homog[0][0]*pts[i][0][0] + 
                           homog[0][1]*pts[i][0][1] + homog[0][2])*div)
            hpts[i][0][1]=((homog[1][0]*pts[i][0][0] + 
                            homog[1][1]*pts[i][0][1] + homog[1][2])*div)
                          
        return hpts 
       
    elif isinstance(pts, list):
        hpts=[]
               
        if inverse:
            val,homog=cv2.invert(homog) 

        for p in pts:
            div=1./(homog[2][0]*p[0]+homog[2][1]*p[1]+homog[2][2])
            xh=(homog[0][0]*p[0]+homog[0][1]*p[1]+homog[0][2])*div
            yh=(homog[1][0]*p[0]+homog[1][1]*p[1]+homog[1][2])*div
            hpts.append([xh,yh])
    else:
        print('PERPECTIVE INPUT: ' + str(type(pts)))
        hpts=None
              
        return hpts 
        

def featureTrack(i0, iN, p0, winsize, back_thresh, min_features):
    '''Function to feature track between two masked images. The Lucas Kanade 
    optical flow algorithm is applied using the OpenCV function 
    calcOpticalFlowPyrLK to find these tracked points in the second image. A 
    backward tracking then tracks back from these to the original points, 
    checking if this is within a given number of pixels as a validation 
    measure. The resulting error is the difference between the original feature 
    point and the backtracked feature point. 
    
    Variables
    i0 (arr):                   Image 1 in the image pair
    iN (arr):                   Image 2 in the image pair
    winsize (tuple):            Window size for tracking e.g. (25,25)
    back_thesh (int):           Threshold for back-tracking distance (i.e.
                                the difference between the original seeded
                                point and the back-tracked point in im0)
    
    Returns
    p1 (arr):                   Point coordinates for points tracked to image 2
    p0r (arr):                  Point coordinates for points back-tracked
                                from image 2 to image 1
    error (arr):                SNR measurements for the corresponding tracked 
                                point. The signal is the magnitude of the 
                                displacement from p0 to p1, and the noise is 
                                the magnitude of the displacement from p0r to 
                                p0
    '''
    #Feature tracking set-up parameters
    lk_params = dict( winSize  = winsize,
                      maxLevel = 2,
                      criteria = (cv2.TERM_CRITERIA_EPS | 
                                  cv2.TERM_CRITERIA_COUNT, 10, 0.03))
        
    #Track forward from im0 to im1. p1 is returned as an array of shape
    #(n,1,2), where n is the number of features tracked
    p1, status1, error1  = cv2.calcOpticalFlowPyrLK(i0, iN, p0, 
                                                    None, **lk_params) 
                                                    
    #Track backwards from im1 to im0 using the forward-tracked points
    p0r, status0, error0  = cv2.calcOpticalFlowPyrLK(iN, i0, p1, 
                                                     None, **lk_params)         
   
    #Find euclidian pixel distance beween original(p0) and backtracked 
    #(p0r) points and discard point greater than the threshold. This is 
    #a way of checking tracking robustness
    dist=(p0-p0r)*(p0-p0r)
    dist=np.sqrt(dist[:,0,0]+dist[:,0,1])            
    tracked=len(dist)
    good = dist < back_thresh
    
    #Points are boolean filtered by the backtracking success 
    p0=p0[good]
    p1=p1[good]
    p0r=p0r[good]
    error=dist[good]
    print('Average back-tracking difference: ' + str(np.mean(good)))

    #Return None if number of tracked features is under the 
    #min_features threshold
    if p0.shape[0]<min_features:
        print('Not enough features successfully tracked.')
        return None
           
    print(str(tracked) + ' features tracked')
    print(str(p0.shape[0]) +' features remaining after forward-backward error')
       
    return [p0,p1,p0r], error


def templateMatch(im0, im1, uv0, templatesize, searchsize, min_features=4, 
                  method=cv2.TM_CCORR_NORMED):
    '''Function to template match between two images. Templates in the first
    image (im0) are generated from a given set of points (uv0) and matched to 
    the search window in image 2 (im1). There are a series of methods that can
    be used for matching, in adherence with those offered with OpenCV's 
    matchTemplate function. After matching, the origin point of each matched 
    template in image 2 is returned, along with the average correlation in 
    each template.
    
    Variables
    im0 (arr):                   Image 1 in the image pair.
    im1 (arr):                   Image 2 in the image pair.
    uv0 (tuple):                 Grid points for image 1.
    templatesize (int):          Pixel dimensions of the template size, given
                                 as a single value (i.e. each template is a 
                                 square) 
    searchsize(int):             Pixel dimensions of the search size, given as 
                                 a single value (i.e. each search window is a 
                                 square) 
    min_features (int):          Minimum number of point tracks to return
    method (int):                Method of correlation:
                                 cv2.TM_CCOEFF: Cross-coefficient
                                 cv2.TM_CCOEFF_NORMED: Normalised cross-coeff
                                 cv2.TM_CCORR - Cross correlation
                                 cv2.TM_CCORR_NORMED - Normalised cross-corr
                                 cv2.TM_SQDIFF - Square difference
                                 cv2.TM_SQDIFF_NORMED - Normalised square diff
    
    Returns
    p1 (arr):                   Point coordinates for points tracked to image 2
    p0r (arr):                  Point coordinates for points back-tracked
                                from image 2 to image 1
    error (arr):                SNR measurements for the corresponding tracked 
                                point. The signal is the magnitude of the 
                                displacement from p0 to p1, and the noise is 
                                the magnitude of the displacement from p0r to 
                                p0
    '''
    #Create empty outputs
    avercorr=[]
    pu2=[]
    pv2=[]        
    
    #Iterate through points
    for u,v in zip(uv0[:,:,0], uv0[:,:,1]):
         
        #Get template and search window for point
        template = im0[int(v-(templatesize/2)):int(v+(templatesize/2)), 
                      int(u-(templatesize/2)):int(u+(templatesize/2))]
        search = im1[int(v-(searchsize/2)):int(v+(searchsize/2)), 
                    int(u-(searchsize/2)):int(u+(searchsize/2))]       
               
        #Change array values from float64 to uint8
        template = template.astype(np.uint8)
        search = search.astype(np.uint8)
                      
        #Define method string as mapping object
        meth=eval(method)
                   
        #Attempt to match template in imageB 
        try:
            resz = cv2.matchTemplate(search, template, meth)
        except:
            resz=None
        
        if resz.all() is not None:
                                
            #Create UV meshgrid for correlation result 
            resx = np.arange(0, resz.shape[1], 1)
            resy = np.arange(0, resz.shape[0], 1)                    
            resx,resy = np.meshgrid(resx, resy, sparse=True)
                                                    
            #Create bicubic interpolation grid                                                                            
            interp = interpolate.interp2d(resx, resy, resz, kind='cubic')                    
            
            #Create sub-pixel UV grid to interpolate across
            subpx = 0.01
            newx = np.arange(0, resz.shape[1], subpx)
            newy = np.arange(0, resz.shape[0], subpx)
                    
            #Interpolate new correlation grid
            resz = interp(newx, newy)
                                                    
            #Get correlation values and coordinate locations        
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(resz)
                                                                                        
            #If the method is TM_SQDIFF or TM_SQDIFF_NORMED, take minimum 
            #location
            if method == 'cv2.TM_SQDIFF':                            
                location = min_loc
            elif method == 'cv2.TM_SQDIFF_NORMED':
                location = min_loc
                
            #Else, take maximum location
            else:                 
                location = max_loc
                                
            #Calculate tracked point location, assuming the origin of the 
            #template window is the same as the origin of the correlation array                    
            loc_x = ((u - ((resz.shape[1]*subpx)/2)) + 
                    (location[0]*subpx))
            loc_y = ((v - ((resz.shape[1]*subpx)/2) + 
                    (location[1]*subpx)))                            
    
            #Retain correlation and location            
            avercorr.append(np.mean(resz))
            pu2.append(loc_x)
            pv2.append(loc_y)
    
    #Reshape all points and average correlations in 3D arrays
    uv1 = np.column_stack([pu2, pv2])            
    uv1 = np.array(uv1, dtype='float32').reshape((-1,1,2))
    avercorr = np.array(avercorr, dtype='float32').reshape((-1,1,1))
    
    #Return none if not enough templates were matched, else return all
    if uv1.shape[0]<min_features:
        print('Not enough templates successfully matched.')
        return None, None
    else:
        print('Average template correlation: ' + str(np.mean(avercorr)))               
        print(str(uv1.shape[0]) + ' templates tracked')
        return [uv0, uv1, None], avercorr


def seedCorners(im, mask, maxpoints, quality, mindist, min_features):
    '''Function to seed corner features using the Shi-Tomasi corner feature 
    detection method in OpenCV's goodFeaturesToTrack function. 
        
    Variables
    im (arr):                   Image for seeding corner points
    mask (arr):                 Image mask to seed points in
    maxpoints (int):            Maximum number of corner points to seed
    quality (int):              Corner feature quality
    mindist (int):              Minimum distance between seeded points                
    min_features (int):         Minimum number of seeded points to track
    
    Returns
    p0 (arr):                   Point coordinates for corner features seeded in 
                                image
    '''    
    #Find corners of the first image. p0 is returned as an array of shape 
    #(n,1,2), where n is the number of features identified 
    if mask is not None:       
        p0=cv2.goodFeaturesToTrack(im,maxpoints,quality,mindist,mask=mask)
    else:
        p0=cv2.goodFeaturesToTrack(im,maxpoints,quality,mindist)
        
    #tracked is the number of features returned by goodFeaturesToTrack        
    tracked=p0.shape[0]
            
    #Check if there are enough points to initially track 
    if tracked<min_features:
        print('Not enough features found to track. Found: ' + str(len(p0)))
        return None
    else:
        return p0


def seedGrid(dem, griddistance, projvars, mask):
    '''Define pixel grid at a specified grid distance, taking into 
    consideration the image size and image mask.
    
    Input variables:
    dem (ExplicitRaster):   DEM object.
    griddistance (list):    The row and column spacing of the grid.
    projvars (list):        Projection variables (camera location, camera pose,
                            radial distortion coefficients, tangential 
                            distortion coefficients, focal length, principal
                            point, reference image path).
    mask (bool):            Boolean denoting image mask
    
    Returns:
    xyz (arr):              Grid point array in DEM space
    uv (arr):               Grid points in 
    '''
    #Get DEM z values
    demz = dem.getZ()

    #Get mask and fill masked demz values with NaN values
    if mask is not None:
        demz = ma.masked_array(demz, np.logical_not(mask))
        demz = demz.filled(np.nan) 
        
    #Get DEM extent
    extent = dem.getExtent()
    
    #Define point spacings in dem space
    samplex = round((extent[1]-extent[0])/griddistance[0])
    sampley = round((extent[3]-extent[2])/griddistance[1])
    
    #Define grid in dem space
    linx = np.linspace(extent[0], extent[1], samplex)
    liny = np.linspace(extent[2], extent[3], sampley)
    
    #Create mesh of grid points
    meshx, meshy = np.meshgrid(linx, liny) 
    
    #Get unique DEM row and column values   
    demx = dem.getData(0)    
    demx_uniq = demx[0,:]
    demx_uniq = demx_uniq.reshape(demx_uniq.shape[0],-1)    
    demy = dem.getData(1)
    demy_uniq = demy[:,0]    
    demy_uniq = demy_uniq.reshape(demy_uniq.shape[0],-1)
    
    #Get Z values for mesh grid
    meshx2 = []
    meshy2 = []
    meshz2 = []

    #Go through all positions in mesh grid    
    for a,b in zip(meshx.flatten(), meshy.flatten()):

        #Find mesh grid point in DEM and return indexes
        indx_x = (np.abs(demx_uniq-a)).argmin()
        indx_y = (np.abs(demy_uniq-b)).argmin()

        #Append Z value if not NaN (i.e. masked out in DEM)
        if np.isnan(demz[indx_y,indx_x]) == False:
            meshx2.append(a)
            meshy2.append(b)
            meshz2.append(demz[indx_y,indx_x])
    
    #Compile grid X, Y, Z components together
    xyz=np.column_stack([meshx2,meshy2,meshz2])

    #Project xyz grid to image plane
    uv,depth,inframe = projectXYZ(projvars[0], projvars[1], projvars[2], 
                                  projvars[3], projvars[4], projvars[5], 
                                  projvars[6], xyz)
    
    #Reshape UV array, 
    uv = np.array(uv, dtype='float32').reshape((-1,1,2))  
    
    return xyz, uv

def readDEMmask(dem, img, invprojvars, demMaskPath=None):
    '''Read/generate DEM mask for subsequent grid generation. If a valid 
    filename is given then the DEM mask is loaded from file. If the filename
    does not exist, then the mask is defined. To define the DEM mask, a mask is
    first defined in the image plane (using point and click, facilitated 
    through Matplotlib Pyplot's ginput function), and then projected to the 
    DEM scene using CamEnv's projectXYZ function. For the projection to work,
    the invprojvars need to be valid X,Y,Z,uv0 parameters, as generated in 
    CamEnv's setProjection function. The mask is saved to file if a filepath is
    given. This DEM mask can be used for dense feature-tracking/template 
    matching, where masked regions of the DEM are reassigned to 
    NaN using Numpy's ma.mask function.
    
    Input variables:
    dem (ExplicitRaster):       Input DEM object.
    img (arr):                  List containing image mask points.
    invprojvars (list):         Inverse projection variables (X,Y,Z,uv0).
    demMaskPath (str):          Path to outputted mask file.
    
    Returns
    demMask (arr):              Boolean visibility matrix (which is the same 
                                size as the dem)
    '''    
    #Check if a DEM mask already exists, if not enter digitising
    if demMaskPath!=None:
        try:
            demMask = Image.open(demMaskPath)
            demMask = np.array(demMask)
            print('\nDEM mask loaded')
            return demMask
        except:
            print('\nDEM mask file not found. Proceeding to manually digitise...')
    
    #Open image in figure plot 
    fig=plt.gcf()
    fig.canvas.set_window_title('Click to create mask. Press enter to record' 
                                ' points.')
    imgplot = plt.imshow(img, origin='upper')
    imgplot.set_cmap('gray')
    
    #Initiate interactive point and click
    uv = plt.ginput(n=0, timeout=0, show_clicks=True, mouse_add=1, mouse_pop=3, 
                    mouse_stop=2)
    print('\n' + str(len(uv)) + ' points seeded')
    plt.show()
    plt.close()
    
    #Close shape
    uv.append(uv[0])
    
    #Reshape array and project to DEM    
    uv = np.array(uv).reshape(-1,2)
    xyz = projectUV(uv, invprojvars)
    xyz = np.column_stack([xyz[:,0], xyz[:,1]]) 
    
    #Get unique row and column data from DEM
    demx = dem.getData(0)    
    demx_uniq = demx[0,:]
    demx_uniq = demx_uniq.reshape(demx_uniq.shape[0],-1)    
    demy = dem.getData(1)
    demy_uniq = demy[:,0] 
    demy_uniq = demy_uniq.reshape(demy_uniq.shape[0],-1)
    
    #Create meshgrid of DEM XY coordinates
    x, y = np.meshgrid(demx_uniq, demy_uniq)
    x, y = x.flatten(), y.flatten()
    points = np.vstack((x,y)).T
    
    #Overlay mask onto meshgrid and reshape as DEM
    poly = path.Path(xyz)
    demMask = poly.contains_points(points)
    demMask = demMask.reshape((demy_uniq.shape[0], demx_uniq.shape[0]))
    
    #Save mask to file if file path is specified
    if demMaskPath != None:
        try:
            Image.fromarray(demMask).convert('L').save(demMaskPath)
            print('\nSaved DEM mask to: ' + str(demMaskPath))
        except:
            print('\nFailed to write file: ' + str(demMaskPath))
        
    return demMask